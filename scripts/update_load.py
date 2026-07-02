import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from supabase import create_client
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()

# Configurações
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "earthquakes_full_record"
USGS_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "3.0"))

# Verifica se as variáveis estão configuradas
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Erro: Configure SUPABASE_URL e SUPABASE_KEY no arquivo .env")
    exit(1)

# Conecta ao Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cache para não repetir dados
CACHE_FILE = "last_run.json"

def get_last_run():
    """Pega a data da última execução"""
    try:
        with open(CACHE_FILE, 'r') as f:
            data = json.load(f)
            return data.get('last_run')
    except:
        return None

def save_last_run(date_str):
    """Salva a data da última execução"""
    with open(CACHE_FILE, 'w') as f:
        json.dump({'last_run': date_str}, f)

def fetch_earthquakes(starttime):
    """Busca terremotos da API do USGS"""
    params = {
        "format": "geojson",
        "starttime": starttime,
        "endtime": datetime.now(timezone.utc).isoformat(),
        "minmagnitude": MIN_MAGNITUDE
    }
    
    print(f"📡 Buscando dados desde {starttime}...")
    
    try:
        response = requests.get(USGS_API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        features = data.get('features', [])
        print(f"✅ Encontrados {len(features)} registros")
        return features
    except Exception as e:
        print(f"❌ Erro: {e}")
        return []

def process_data(features):
    """Processa os dados para enviar ao Supabase"""
    records = []
    
    for quake in features:
        prop = quake['properties']
        geo = quake['geometry']
        coords = geo['coordinates']
        
        # Converte timestamp para data
        event_time = None
        if prop.get('time'):
            event_time = datetime.fromtimestamp(
                prop['time'] / 1000.0, 
                tz=timezone.utc
            ).isoformat()
        
        records.append({
            'event_id': quake['id'],
            'event_time': event_time,
            'latitude': coords[1] if len(coords) > 1 else None,
            'longitude': coords[0] if len(coords) > 0 else None,
            'depth_km': coords[2] if len(coords) > 2 else None,
            'magnitude': prop.get('mag'),
            'place': prop.get('place'),
            'tsunami': prop.get('tsunami', 0),
            'felt': prop.get('felt', 0)
        })
    
    return records

def run_update():
    print("🚀 INICIANDO PIPELINE DE TERREMOTOS")
    print("=" * 50)
    
    # Define a data de início
    last_run = get_last_run()
    
    if last_run:
        # Subtrai 1 hora para não perder nenhum evento
        dt = datetime.fromisoformat(last_run.replace('Z', '+00:00'))
        dt = dt - timedelta(hours=1)
        starttime = dt.isoformat()
        print(f"📅 Última execução: {last_run}")
    else:
        # Primeira execução: busca últimos 30 dias
        starttime = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        print(f"📅 Primeira execução. Buscando últimos 30 dias.")
    
    # Busca os dados
    features = fetch_earthquakes(starttime)
    
    if not features:
        print("ℹ️ Nenhum dado novo encontrado")
        return
    
    # Processa os dados
    records = process_data(features)
    
    if not records:
        print("ℹ️ Nenhum registro válido")
        return
    
    # Envia para o Supabase
    try:
        # Envia em lotes de 500
        for i in range(0, len(records), 500):
            batch = records[i:i+500]
            supabase.table(TABLE_NAME).upsert(batch).execute()
            print(f"✅ Lote {i//500 + 1}: {len(batch)} registros enviados")
        
        # Salva a data da última execução
        save_last_run(datetime.now(timezone.utc).isoformat())
        print(f"✅ Total: {len(records)} registros processados com sucesso!")
        
    except Exception as e:
        print(f"❌ Erro ao enviar para o Supabase: {e}")

if __name__ == "__main__":
    run_update()