# ==========================================================
# INCREMENTAL UPDATE LOAD - EARTHQUAKES (GITHUB ACTIONS)
# ==========================================================

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
# CONFIGURAÇÕES (IGUAIS AO HISTÓRICO)
# ==========================================
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "earthquakes_full_record"
USGS_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Filtros
MIN_MAGNITUDE = 3.0
MAX_MAGNITUDE = None
MIN_DEPTH = None
MAX_DEPTH = None

# Parâmetros de deduplicação (MESMOS DO HISTÓRICO)
LAT_LON_ROUND = 0      # 0 = ~111 km
TIME_WINDOW = '1h'     # janela de 1 hora

# Performance
BATCH_SIZE = 500
CACHE_FILE = "last_run_cache.json"
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2

# ==========================================
# VALIDAÇÃO DE AMBIENTE
# ==========================================
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Erro: Configure SUPABASE_URL e SUPABASE_KEY no .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# FUNÇÕES AUXILIARES (MESMAS DO HISTÓRICO)
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
    """
    Calcula o unified_id exatamente como no script histórico.
    Usa LAT_LON_ROUND e TIME_WINDOW definidos globalmente.
    """
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
# GERENCIAMENTO DE CACHE (ÚLTIMA EXECUÇÃO)
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
    """
    Retorna a data do último evento registrado no banco,
    com margem de 1 hora para trás.
    """
    try:
        print("📡 Buscando último evento no Supabase...")
        response = (
            supabase.table(TABLE_NAME)
            .select("event_time")
            .order("event_time", desc=True)
            .limit(1)
            .execute()
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
# BUSCA DADOS NO USGS (INCREMENTAL)
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
# TRANSFORMAÇÃO DOS DADOS
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

    df["magnitude"] = pd.to_numeric(df["magnitude"], errors="coerce").fillna(0.0)
    df["depth_km"] = pd.to_numeric(df["depth_km"], errors="coerce").fillna(0.0)
    for col in ["felt", "significance", "tsunami"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["event_day"] = pd.to_datetime(df["event_time"], errors='coerce').dt.date.astype(str)
    df["place_clean"] = df["place"].apply(clean_place)
    df["magnitude_tier"] = df["magnitude"].apply(get_magnitude_tier)
    df["depth_category"] = df["depth_km"].apply(get_depth_category)
    df["lat_long"] = df["latitude"].astype(str) + "," + df["longitude"].astype(str)

    valid_mask = df["latitude"].notna() & df["longitude"].notna()
    if valid_mask.any():
        coords_list = list(zip(df.loc[valid_mask, "latitude"], df.loc[valid_mask, "longitude"]))
        rg_results = rg.search(coords_list)
        df.loc[valid_mask, "country_code"] = [r.get("cc") for r in rg_results]
        df["country_name"] = df["country_code"].apply(get_country_name)
        df["continent"] = df["country_code"].apply(get_continent)
    else:
        df["country_code"] = df["country_name"] = df["continent"] = "Unknown"

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object).where(pd.notnull(df), None)

    return df.to_dict(orient='records')

# ==========================================
# MERGE INTELIGENTE NO SUPABASE
# ==========================================
def merge_event(record: dict) -> bool:
    """
    Insere ou atualiza um registro com base no event_id e unified_id.
    - Se event_id já existe: atualiza o registro (merge de campos e listas).
    - Se não existe, mas unified_id existe: faz merge no grupo.
    - Se nenhum existe: insere novo registro.
    """
    event_id = record.get('event_id')
    unified_id = record.get('unified_id')

    if not event_id:
        print("⚠️ Registro sem event_id, ignorado.")
        return False

    # 1. Verifica se o event_id já existe
    try:
        resp = supabase.table(TABLE_NAME).select("*").eq("event_id", event_id).execute()
    except Exception as e:
        print(f"❌ Erro ao consultar event_id {event_id}: {e}")
        return False

    existing_by_event = resp.data[0] if resp.data else None

    if existing_by_event:
        # Já existe um registro com este event_id -> atualiza
        # Prepara os dados de atualização (mesmo merge, mas usando o registro existente como base)
        existing_ids = existing_by_event.get('original_event_ids', [])
        existing_links = existing_by_event.get('original_links', [])

        # Se o event_id já está na lista, não precisa adicionar novamente
        if event_id not in existing_ids:
            new_ids = existing_ids + [event_id]
            new_links = existing_links + ([record.get('official_link')] if record.get('official_link') else [])
        else:
            new_ids = existing_ids
            new_links = existing_links

        update_data = {
            'original_event_ids': new_ids,
            'original_links': new_links,
            'unified_count': existing_by_event.get('unified_count', 0) + 1,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }

        # Se a nova magnitude for maior, substitui os campos principais
        new_mag = record.get('magnitude', 0)
        old_mag = existing_by_event.get('magnitude', 0)
        if new_mag > old_mag:
            for field in ['event_time', 'place', 'latitude', 'longitude', 'depth_km',
                          'significance', 'alert', 'tsunami', 'felt', 'event_type',
                          'event_day', 'place_clean', 'magnitude_tier', 'depth_category',
                          'lat_long', 'country_code', 'country_name', 'continent']:
                if field in record:
                    update_data[field] = record[field]
            update_data['magnitude'] = record['magnitude']
            update_data['official_link'] = record.get('official_link')
            # Atualiza também o unified_id, caso tenha mudado (pode acontecer se os parâmetros mudarem)
            if unified_id:
                update_data['unified_id'] = unified_id

        try:
            supabase.table(TABLE_NAME).update(update_data).eq("event_id", event_id).execute()
            # print(f"✅ Registro {event_id} atualizado.")
            return True
        except Exception as e:
            print(f"❌ Erro ao atualizar event_id {event_id}: {e}")
            return False

    # 2. Se event_id não existe, verifica por unified_id
    if not unified_id:
        # Se não tem unified_id, insere como novo (mas com cuidado, pois event_id é único)
        # Mas como event_id não existe, podemos inserir diretamente
        insert_record = record.copy()
        insert_record['unified_count'] = 1
        insert_record['original_event_ids'] = [event_id]
        insert_record['original_links'] = [record.get('official_link')] if record.get('official_link') else []
        try:
            supabase.table(TABLE_NAME).insert(insert_record).execute()
            return True
        except Exception as e:
            print(f"❌ Erro ao inserir evento {event_id}: {e}")
            return False

    # 3. Verifica se o unified_id já existe
    try:
        resp = supabase.table(TABLE_NAME).select("*").eq("unified_id", unified_id).execute()
    except Exception as e:
        print(f"❌ Erro ao consultar unified_id {unified_id}: {e}")
        return False

    existing_by_unified = resp.data[0] if resp.data else None

    if existing_by_unified:
        # Faz o merge no grupo existente (como antes)
        existing_ids = existing_by_unified.get('original_event_ids', [])
        existing_links = existing_by_unified.get('original_links', [])

        if event_id in existing_ids:
            # Já está no grupo, nada a fazer
            return True

        new_ids = existing_ids + [event_id]
        new_links = existing_links + ([record.get('official_link')] if record.get('official_link') else [])

        update_data = {
            'original_event_ids': new_ids,
            'original_links': new_links,
            'unified_count': existing_by_unified.get('unified_count', 0) + 1,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }

        new_mag = record.get('magnitude', 0)
        old_mag = existing_by_unified.get('magnitude', 0)
        if new_mag > old_mag:
            for field in ['event_time', 'place', 'latitude', 'longitude', 'depth_km',
                          'significance', 'alert', 'tsunami', 'felt', 'event_type',
                          'event_day', 'place_clean', 'magnitude_tier', 'depth_category',
                          'lat_long', 'country_code', 'country_name', 'continent']:
                if field in record:
                    update_data[field] = record[field]
            update_data['magnitude'] = record['magnitude']
            update_data['official_link'] = record.get('official_link')

        try:
            supabase.table(TABLE_NAME).update(update_data).eq("unified_id", unified_id).execute()
            return True
        except Exception as e:
            print(f"❌ Erro ao atualizar grupo {unified_id}: {e}")
            return False
    else:
        # 4. Nenhum registro encontrado: insere novo
        insert_record = record.copy()
        insert_record['unified_count'] = 1
        insert_record['original_event_ids'] = [event_id]
        insert_record['original_links'] = [record.get('official_link')] if record.get('official_link') else []
        try:
            supabase.table(TABLE_NAME).insert(insert_record).execute()
            return True
        except Exception as e:
            print(f"❌ Erro ao inserir grupo {unified_id}: {e}")
            return False

# ==========================================
# PIPELINE PRINCIPAL
# ==========================================
def run_update():
    print("=" * 70)
    print("🚀 INICIANDO UPDATE LOAD (GITHUB ACTIONS - DEDUPLICADO)")
    print("=" * 70)
    print(f"📋 Configurações:")
    print(f"   - Magnitude mínima: {MIN_MAGNITUDE}")
    print(f"   - Grade: {LAT_LON_ROUND} casas (~{['111 km','11 km','1.1 km'][LAT_LON_ROUND] if LAT_LON_ROUND <= 2 else 'custom'})")
    print(f"   - Janela temporal: {TIME_WINDOW}")
    print(f"   - Lote: {BATCH_SIZE}")
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

    print(f"📤 Processando {len(records)} eventos...")

    success_count = 0
    for rec in records:
        if merge_event(rec):
            success_count += 1

    if success_count > 0:
        max_time = max([r.get('event_time') for r in records if r.get('event_time')])
        if max_time:
            save_last_run_date(max_time)
            print(f"💾 Cache atualizado com a data: {max_time}")

    print(f"✅ Total de eventos processados com sucesso: {success_count}/{len(records)}")
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