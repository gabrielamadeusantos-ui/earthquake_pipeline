import os
import time
import calendar
import requests
import pandas as pd
import numpy as np
import hashlib
import reverse_geocoder as rg
import pycountry_convert as pc
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

# ==========================================
# --- CONFIGURAÇÕES (AJUSTE AQUI) ---
# ==========================================
load_dotenv(override=True)

STARTING_YEAR = 1996
ENDING_YEAR = 2026
MIN_MAGNITUDE = 4.0                # <-- AUMENTADO PARA 4.0
MAX_MAGNITUDE = None
MIN_DEPTH = None
MAX_DEPTH = None

# --- PARÂMETROS DE DEDUPLICAÇÃO ---
LAT_LON_ROUND = 0                  # 0 = ~111km, 1 = ~11km, 2 = ~1.1km
TIME_WINDOW = '1h'                 # '1h', '3h', '6h', '12h', '1D'

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "earthquakes_full_record"
USGS_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Configure SUPABASE_URL e SUPABASE_KEY no .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# --- FUNÇÕES AUXILIARES ---
# ==========================================
def convert_ms_to_datetime(ms: int) -> str | None:
    if not ms or pd.isna(ms):
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def get_country_name(code: str) -> str:
    try:
        if pd.isna(code) or not code: return "Unknown"
        return pc.country_alpha2_to_country_name(code)
    except:
        return "Unknown"

def get_continent(code: str) -> str:
    try:
        if pd.isna(code) or not code: return "Other"
        continent_code = pc.country_alpha2_to_continent_code(code)
        continents = {
            "NA": "North America", "SA": "South America", "EU": "Europe",
            "AS": "Asia", "AF": "Africa", "OC": "Oceania"
        }
        return continents.get(continent_code, "Other")
    except:
        return "Other"

def clean_place(place_str: str) -> str:
    if pd.isna(place_str): return "Unknown"
    return str(place_str).split(" of ")[-1].strip()

def get_magnitude_tier(mag: float) -> str:
    if pd.isna(mag): return 'Unknown'
    if mag < 4.0: return 'Minor'
    if mag < 5.0: return 'Light'
    if mag < 6.0: return 'Moderate'
    if mag < 7.0: return 'Strong'
    if mag < 8.0: return 'Major'
    return 'Great'

def get_depth_category(depth: float) -> str:
    if pd.isna(depth): return 'Unknown'
    if depth < 70: return 'Shallow'
    if depth < 300: return 'Intermediate'
    return 'Deep'

def clear_table_in_batches():
    print("🗑️ Removendo dados antigos em lotes...")
    batch_size = 1000
    total_removed = 0
    while True:
        try:
            resp = supabase.table(TABLE_NAME).select("event_id").limit(batch_size).execute()
            ids = [r['event_id'] for r in resp.data]
            if not ids:
                break
            supabase.table(TABLE_NAME).delete().in_('event_id', ids).execute()
            total_removed += len(ids)
            print(f"   Removidos {total_removed} registros...")
            time.sleep(0.5)
        except Exception as e:
            if "relation" in str(e).lower() and "does not exist" in str(e).lower():
                print("   ℹ️ Tabela ainda não existe. Nada para limpar.")
                break
            print(f"   ⚠️ Erro na remoção: {e}. Tentando novamente...")
            time.sleep(2)
    print(f"✅ Limpeza concluída. Total removido: {total_removed}")

def fetch_usgs_data(year: int, month: int) -> list:
    last_day = calendar.monthrange(year, month)[1]
    params = {
        "format": "geojson",
        "starttime": f"{year}-{month:02d}-01",
        "endtime": f"{year}-{month:02d}-{last_day}",
        "minmagnitude": MIN_MAGNITUDE
    }
    if MAX_MAGNITUDE is not None: params["maxmagnitude"] = MAX_MAGNITUDE
    if MIN_DEPTH is not None: params["mindepth"] = MIN_DEPTH
    if MAX_DEPTH is not None: params["maxdepth"] = MAX_DEPTH

    print(f"📡 Buscando {month:02d}/{year}...", end=" ")
    try:
        response = requests.get(USGS_API_URL, params=params, timeout=30)
        response.raise_for_status()
        features = response.json().get('features', [])
        print(f"{len(features)} registros.")
        return features
    except Exception as e:
        print(f"❌ Erro: {e}")
        return []

def transform_and_deduplicate(features: list) -> pd.DataFrame:
    records = []
    for quake in features:
        prop = quake['properties']
        geo = quake['geometry']
        coords = geo['coordinates']

        event_time = convert_ms_to_datetime(prop.get('time'))
        lat = coords[1] if len(coords) > 1 else None
        lon = coords[0] if len(coords) > 0 else None
        lat_long = f"{lat},{lon}" if lat and lon else None

        records.append({
            'event_id': quake['id'],
            'official_link': prop.get('url'),
            'last_updated': convert_ms_to_datetime(prop.get('updated')),
            'event_time': event_time,
            'place': prop.get('place'),
            'latitude': lat,
            'longitude': lon,
            'depth_km': coords[2] if len(coords) > 2 else None,
            'magnitude': prop.get('mag'),
            'significance': prop.get('sig'),
            'alert': prop.get('alert'),
            'tsunami': prop.get('tsunami'),
            'felt': prop.get('felt'),
            'event_type': prop.get('type'),
            'lat_long': lat_long
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df

    df["magnitude"] = pd.to_numeric(df["magnitude"], errors="coerce").fillna(0.0)
    df["depth_km"] = pd.to_numeric(df["depth_km"], errors="coerce").fillna(0.0)
    for col in ["felt", "significance", "tsunami"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["event_day"] = pd.to_datetime(df["event_time"], errors='coerce').dt.date.astype(str)
    df["place_clean"] = df["place"].apply(clean_place)
    df["magnitude_tier"] = df["magnitude"].apply(get_magnitude_tier)
    df["depth_category"] = df["depth_km"].apply(get_depth_category)

    valid_coords = df["latitude"].notna() & df["longitude"].notna()
    if valid_coords.any():
        coords_list = tuple(zip(df.loc[valid_coords, "latitude"], df.loc[valid_coords, "longitude"]))
        rg_results = rg.search(coords_list)
        df.loc[valid_coords, "country_code"] = [r.get("cc") for r in rg_results]
        df["country_name"] = df["country_code"].apply(get_country_name)
        df["continent"] = df["country_code"].apply(get_continent)
    else:
        df["country_code"] = df["country_name"] = df["continent"] = "Unknown"

    # ==============================================================
    # --- DEDUPLICAÇÃO COM PARÂMETROS CONFIGURÁVEIS ---
    # ==============================================================
    print(f"   🔄 Aplicando deduplicação (grade {LAT_LON_ROUND} casas, janela {TIME_WINDOW})...", end=" ")
    df['event_time_dt'] = pd.to_datetime(df['event_time'], errors='coerce')
    df = df.dropna(subset=['event_time_dt', 'latitude', 'longitude'])
    if df.empty:
        print("0 registros.")
        return df

    df['lat_rounded'] = df['latitude'].round(LAT_LON_ROUND)
    df['lon_rounded'] = df['longitude'].round(LAT_LON_ROUND)
    df['time_rounded'] = df['event_time_dt'].dt.floor(TIME_WINDOW)

    df['group_key'] = (
        df['lat_rounded'].astype(str) + ',' +
        df['lon_rounded'].astype(str) + '_' +
        df['time_rounded'].astype(str)
    )

    df['unified_id'] = df['group_key'].apply(
        lambda x: hashlib.md5(x.encode()).hexdigest()[:16]
    )

    df_sorted = df.sort_values(['group_key', 'magnitude'], ascending=[True, False])
    df_dedup = df_sorted.drop_duplicates(subset=['group_key'], keep='first')

    group_stats = df.groupby('group_key').agg(
        unified_count=('event_id', 'count'),
        original_event_ids=('event_id', lambda x: list(x)),
        original_links=('official_link', lambda x: list(x))
    ).reset_index()

    df_dedup = df_dedup.merge(group_stats, on='group_key', how='left')
    df_dedup = df_dedup.drop(
        ['event_time_dt', 'time_rounded', 'group_key', 'lat_rounded', 'lon_rounded'],
        axis=1
    )

    print(f"{len(df_dedup)} registros únicos.")
    return df_dedup

def load_to_supabase_with_retry(df: pd.DataFrame, batch_size=200, max_retries=3):
    if df.empty:
        print("   Nenhum dado para carregar.")
        return True

    df_clean = df.replace([np.inf, -np.inf], np.nan)
    df_clean = df_clean.astype(object).where(pd.notnull(df_clean), None)
    records = df_clean.to_dict(orient='records')

    print(f"   📤 Carregando {len(records)} registros (lotes de {batch_size})...")
    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        for attempt in range(max_retries):
            try:
                supabase.table(TABLE_NAME).insert(batch).execute()
                print(f"   ✅ Lote {i//batch_size + 1}: {len(batch)} registros inseridos.")
                break
            except Exception as e:
                print(f"   ⚠️ Erro no lote {i//batch_size + 1} (tentativa {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1))
                else:
                    print(f"   ❌ Lote {i//batch_size + 1} falhou após {max_retries} tentativas.")
                    return False
    return True

def run_pipeline():
    print(f"🚀 Iniciando pipeline com deduplicação (Ano {STARTING_YEAR} a {ENDING_YEAR})")
    print(f"   Magnitude mínima: {MIN_MAGNITUDE}")
    print(f"   Grade: {LAT_LON_ROUND} casas decimais (~{['111 km', '11 km', '1.1 km'][LAT_LON_ROUND] if LAT_LON_ROUND <= 2 else 'custom'})")
    print(f"   Janela temporal: {TIME_WINDOW}")
    print("=" * 60)

    clear_table_in_batches()

    current_year = datetime.now().year
    current_month = datetime.now().month

    for year in range(STARTING_YEAR, ENDING_YEAR + 1):
        for month in range(1, 13):
            if year == current_year and month > current_month:
                break
            if year > current_year:
                break

            features = fetch_usgs_data(year, month)
            if not features:
                time.sleep(0.5)
                continue

            df = transform_and_deduplicate(features)
            if df.empty:
                continue

            sucesso = load_to_supabase_with_retry(df, batch_size=200, max_retries=3)
            if not sucesso:
                print("   ❌ Falha na carga, interrompendo...")
                return

            time.sleep(1)

    print("\n✅ PIPELINE CONCLUÍDO COM SUCESSO!")

if __name__ == "__main__":
    run_pipeline()