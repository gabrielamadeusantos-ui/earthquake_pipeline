import os
import json
import requests
import pandas as pd
import numpy as np
import reverse_geocoder as rg
import pycountry_convert as pc
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv
import time
import hashlib

# ==========================================
# CONFIGURAÇÕES
# ==========================================
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "earthquakes_full_record"
USGS_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", 3.0))
MAX_MAGNITUDE = None
MIN_DEPTH = None
MAX_DEPTH = None

LAT_LON_ROUND = 0          # 0 = ~111 km
TIME_WINDOW = '1h'
BATCH_SIZE = 500
CACHE_FILE = "last_run_cache.json"
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2

# ==========================================
# VALIDAÇÃO
# ==========================================
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Erro: Configure SUPABASE_URL e SUPABASE_KEY no .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# FUNÇÕES AUXILIARES (sem alterações)
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

def compute_unified_id(lat: float, lon: float, event_time: str) -> str:
    if pd.isna(lat) or pd.isna(lon) or not event_time:
        return None
    dt = pd.to_datetime(event_time, errors='coerce')
    if pd.isna(dt):
        return None
    lat_rounded = round(lat, LAT_LON_ROUND)
    lon_rounded = round(lon, LAT_LON_ROUND)
    time_rounded = dt.floor(TIME_WINDOW)
    group_key = f"{lat_rounded},{lon_rounded}_{time_rounded.isoformat()}"
    return hashlib.md5(group_key.encode()).hexdigest()[:16]

# ==========================================
# GERENCIAMENTO DE CACHE
# ==========================================
def save_last_run_date(date_str: str):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump({"last_run": date_str, "timestamp": datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"⚠️ Não foi possível salvar cache: {e}")

def load_last_run_date() -> str | None:
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                return data.get("last_run")
    except Exception as e:
        print(f"⚠️ Não foi possível ler cache: {e}")
    return None

def get_last_event_time() -> str | None:
    try:
        print("📡 Buscando último evento no Supabase...")
        response = (
            supabase.table(TABLE_NAME)
            .select("event_time")
            .order("event_time", desc=True)
            .limit(1)
            .execute()  # sem timeout
        )
        data = response.data
        if data and data[0].get("event_time"):
            last_time = data[0]["event_time"]
            dt = datetime.fromisoformat(last_time.replace('Z', '+00:00'))
            dt = dt - timedelta(hours=1)
            result = dt.isoformat()
            save_last_run_date(result)
            print(f"✅ Último evento encontrado: {result}")
            return result
    except Exception as e:
        print(f"⚠️ Falha na consulta ao banco: {e}")

    cached = load_last_run_date()
    if cached:
        print(f"📁 Última data do cache: {cached}")
        return cached

    print("⚠️ Nenhum dado encontrado. Usando fallback de 30 dias.")
    return None

# ==========================================
# BUSCA DADOS NO USGS
# ==========================================
def fetch_usgs_incremental(starttime: str) -> list:
    params = {
        "format": "geojson",
        "starttime": starttime,
        "endtime": datetime.now(timezone.utc).isoformat(),
        "minmagnitude": MIN_MAGNITUDE
    }
    if MAX_MAGNITUDE is not None: params["maxmagnitude"] = MAX_MAGNITUDE
    if MIN_DEPTH is not None: params["mindepth"] = MIN_DEPTH
    if MAX_DEPTH is not None: params["maxdepth"] = MAX_DEPTH

    print(f"📡 Buscando dados desde {starttime}...")

    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = requests.get(USGS_API_URL, params=params, timeout=30)
            response.raise_for_status()
            features = response.json().get('features', [])
            print(f"📊 {len(features)} novos registros encontrados.")
            return features
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout (tentativa {attempt+1}/{RETRY_ATTEMPTS})")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            print(f"❌ Erro (tentativa {attempt+1}): {e}")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
    print("❌ Falha ao buscar dados do USGS.")
    return []

# ==========================================
# TRANSFORMAÇÃO E ENRIQUECIMENTO (CORRIGIDA)
# ==========================================
def transform_and_enrich(features: list) -> list:
    if not features:
        return []

    records = []
    for quake in features:
        prop = quake['properties']
        geo = quake['geometry']
        coords = geo['coordinates']

        event_time = convert_ms_to_datetime(prop.get('time'))
        lat = coords[1] if len(coords) > 1 else None
        lon = coords[0] if len(coords) > 0 else None
        unified_id = compute_unified_id(lat, lon, event_time)

        rec = {
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
            'unified_id': unified_id,
        }
        records.append(rec)

    df = pd.DataFrame(records)

    # Conversões numéricas
    df["magnitude"] = pd.to_numeric(df["magnitude"], errors="coerce").fillna(0.0)
    df["depth_km"] = pd.to_numeric(df["depth_km"], errors="coerce").fillna(0.0)
    for col in ["felt", "significance", "tsunami"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Data do evento – trata NaT corretamente
    df["event_day"] = pd.to_datetime(df["event_time"], errors='coerce').dt.date
    df["event_day"] = df["event_day"].apply(lambda x: x.isoformat() if pd.notna(x) else None)

    df["place_clean"] = df["place"].apply(clean_place)
    df["magnitude_tier"] = df["magnitude"].apply(get_magnitude_tier)
    df["depth_category"] = df["depth_km"].apply(get_depth_category)
    df["lat_long"] = df["latitude"].astype(str) + "," + df["longitude"].astype(str)

    # Geocodificação
    valid_mask = df["latitude"].notna() & df["longitude"].notna()
    if valid_mask.any():
        coords_list = list(zip(df.loc[valid_mask, "latitude"], df.loc[valid_mask, "longitude"]))
        rg_results = rg.search(coords_list)
        df.loc[valid_mask, "country_code"] = [r.get("cc") for r in rg_results]
        df["country_name"] = df["country_code"].apply(get_country_name)
        df["continent"] = df["country_code"].apply(get_continent)
    else:
        df["country_code"] = df["country_name"] = df["continent"] = "Unknown"

    # Substitui NaNs por None para JSON
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object).where(pd.notnull(df), None)

    return df.to_dict(orient='records')

# ==========================================
# FUNÇÕES DE CONSULTA EM LOTE (SEM TIMEOUT)
# ==========================================
def fetch_existing_by_ids(ids_list, column, table=TABLE_NAME):
    """Retorna dicionário {id: registro} para os IDs, consultando em lotes de 200."""
    result = {}
    batch_size = 200
    for i in range(0, len(ids_list), batch_size):
        batch = ids_list[i:i+batch_size]
        try:
            resp = supabase.table(table).select("*").in_(column, batch).execute()  # sem timeout
            for row in resp.data:
                result[row[column]] = row
        except Exception as e:
            print(f"⚠️ Erro ao buscar lote de {column}: {e}")
    return result

# ==========================================
# MERGE EM LOTE (SEM TIMEOUT)
# ==========================================
def batch_merge(records: list) -> int:
    if not records:
        return 0

    # 1. Extrair IDs
    event_ids = [r['event_id'] for r in records if r.get('event_id')]
    unified_ids = [r['unified_id'] for r in records if r.get('unified_id')]

    # 2. Buscar existentes por event_id (em lotes)
    print(f"🔍 Buscando {len(event_ids)} event_ids no banco...")
    existing_by_event = fetch_existing_by_ids(event_ids, 'event_id')

    # 3. Buscar existentes por unified_id (apenas para os que não foram encontrados por event_id)
    unified_ids_to_fetch = []
    for rec in records:
        uid = rec.get('unified_id')
        eid = rec.get('event_id')
        if uid and eid not in existing_by_event:
            unified_ids_to_fetch.append(uid)
    print(f"🔍 Buscando {len(unified_ids_to_fetch)} unified_ids no banco...")
    existing_by_unified = fetch_existing_by_ids(unified_ids_to_fetch, 'unified_id')

    # 4. Classificar registros
    to_insert = []
    to_update_by_event = []
    to_update_by_unified = []

    for rec in records:
        eid = rec['event_id']
        uid = rec.get('unified_id')

        if eid in existing_by_event:
            to_update_by_event.append(rec)
        elif uid and uid in existing_by_unified:
            to_update_by_unified.append(rec)
        else:
            to_insert.append(rec)

    print(f"📌 Classificação: {len(to_insert)} novos, {len(to_update_by_event)} atualizar por event_id, {len(to_update_by_unified)} atualizar por unified_id")

    # 5. Inserir novos em lote
    if to_insert:
        insert_records = []
        for rec in to_insert:
            insert_rec = rec.copy()
            insert_rec['unified_count'] = 1
            insert_rec['original_event_ids'] = [rec['event_id']]
            insert_rec['original_links'] = [rec.get('official_link')] if rec.get('official_link') else []
            insert_records.append(insert_rec)
        try:
            supabase.table(TABLE_NAME).insert(insert_records).execute()  # sem timeout
            print(f"✅ Inseridos {len(to_insert)} novos registros em lote.")
        except Exception as e:
            print(f"❌ Erro ao inserir lote: {e}")
            # Fallback: inserir individualmente
            for rec in insert_records:
                try:
                    supabase.table(TABLE_NAME).insert(rec).execute()  # sem timeout
                except Exception as e2:
                    print(f"❌ Falha ao inserir {rec['event_id']}: {e2}")

    # 6. Atualizar por event_id
    for rec in to_update_by_event:
        existing = existing_by_event[rec['event_id']]
        update_data = build_update_data(existing, rec)
        try:
            supabase.table(TABLE_NAME).update(update_data).eq("event_id", rec['event_id']).execute()  # sem timeout
        except Exception as e:
            print(f"❌ Erro ao atualizar event_id {rec['event_id']}: {e}")

    # 7. Atualizar por unified_id
    for rec in to_update_by_unified:
        existing = existing_by_unified[rec['unified_id']]
        update_data = build_update_data(existing, rec)
        try:
            supabase.table(TABLE_NAME).update(update_data).eq("unified_id", rec['unified_id']).execute()  # sem timeout
        except Exception as e:
            print(f"❌ Erro ao atualizar unified_id {rec['unified_id']}: {e}")

    return len(records)

def build_update_data(existing: dict, new: dict) -> dict:
    existing_ids = existing.get('original_event_ids', [])
    existing_links = existing.get('original_links', [])
    new_id = new['event_id']
    new_link = new.get('official_link')

    if new_id not in existing_ids:
        new_ids = existing_ids + [new_id]
        new_links = existing_links + ([new_link] if new_link else [])
    else:
        new_ids = existing_ids
        new_links = existing_links

    update_data = {
        'original_event_ids': new_ids,
        'original_links': new_links,
        'unified_count': existing.get('unified_count', 0) + 1,
        'last_updated': datetime.now(timezone.utc).isoformat()
    }

    # Atualiza campos principais se nova magnitude for maior
    new_mag = new.get('magnitude', 0)
    old_mag = existing.get('magnitude', 0)
    if new_mag > old_mag:
        for field in ['event_time', 'place', 'latitude', 'longitude', 'depth_km',
                      'significance', 'alert', 'tsunami', 'felt', 'event_type',
                      'event_day', 'place_clean', 'magnitude_tier', 'depth_category',
                      'lat_long', 'country_code', 'country_name', 'continent']:
            if field in new and new[field] is not None:
                update_data[field] = new[field]
        update_data['magnitude'] = new['magnitude']
        update_data['official_link'] = new.get('official_link')
        if new.get('unified_id'):
            update_data['unified_id'] = new['unified_id']
    return update_data

# ==========================================
# PIPELINE PRINCIPAL
# ==========================================
def run_update():
    print("=" * 70)
    print("🚀 INICIANDO UPDATE LOAD (OTIMIZADO - SEM TIMEOUT)")
    print("=" * 70)
    print(f"📋 Configurações:")
    print(f"   - Magnitude mínima: {MIN_MAGNITUDE}")
    print(f"   - Grade: {LAT_LON_ROUND} casas")
    print(f"   - Janela temporal: {TIME_WINDOW}")
    print("=" * 70)

    start_time = get_last_event_time()
    if not start_time:
        start_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        print(f"⚠️ Usando fallback: últimos 30 dias a partir de {start_time}")

    features = fetch_usgs_incremental(start_time)
    if not features:
        print("ℹ️ Nenhum dado novo encontrado.")
        return

    records = transform_and_enrich(features)
    if not records:
        print("ℹ️ Nenhum registro válido.")
        return

    print(f"📤 Processando {len(records)} eventos em lote...")
    t0 = time.time()
    success_count = batch_merge(records)
    elapsed = time.time() - t0

    if success_count > 0:
        # Atualiza cache com a data do evento mais recente
        max_time = max([r.get('event_time') for r in records if r.get('event_time')])
        if max_time:
            save_last_run_date(max_time)
            print(f"💾 Cache atualizado com a data: {max_time}")

    print(f"✅ Total de eventos processados: {success_count}/{len(records)} em {elapsed:.2f} segundos")
    print("=" * 70)
    print("✅ PIPELINE FINALIZADO!")

if __name__ == "__main__":
    try:
        run_update()
    except KeyboardInterrupt:
        print("\n⚠️ Processo interrompido pelo usuário")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()