import time
import re
import openrouteservice
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# --- CONFIGURAÇÃO ---
ORS_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjdiNTdjYTc2ODQ2OTQ5NmQ4ZmI1OTEwMWIwY2M2MzNlIiwiaCI6Im11cm11cjY0In0='  # <--- COLOQUE SUA CHAVE AQUI
USER_AGENT = "fuel_tester_waterfall_v1"

# Inicializa clientes
geolocator = Nominatim(user_agent=USER_AGENT, timeout=10)
try:
    ors_client = openrouteservice.Client(key=ORS_API_KEY)
except:
    print("⚠️ AVISO: Chave ORS inválida ou ausente. A etapa 2 vai falhar.")
    ors_client = None

# --- FUNÇÕES AUXILIARES ---

def clean_highway_address(raw_address):
    """ Limpa o endereço para o Nominatim (Tentativa 1) """
    if not raw_address: return ""
    # Remove EXIT, MM, AT MILE seguido de alphanuméricos/hifens
    cleaned = re.sub(r'(?:EXIT|MM|Ex|AT\s+MILE)\s*[\w\d\-\s]+', '', raw_address, flags=re.IGNORECASE)
    cleaned = cleaned.replace('&', ' and ').replace('/', ' and ')
    cleaned = re.sub(r',\s*and', ' and', cleaned, flags=re.IGNORECASE) # Remove virgula antes do and
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().strip(' ,')
    return cleaned

def get_city_coordinates(city, state):
    """ Busca coordenadas da cidade (Necessário para a etapa 2 e 3) """
    query = f"{city}, {state}, USA"
    try:
        return geolocator.geocode(query, exactly_one=True, timeout=5)
    except:
        return None

def find_poi_by_name(name, focus_lat, focus_lon):
    """ Busca o posto pelo NOME perto da cidade (Tentativa 2) """
    if not ors_client: return None
    try:
        # ORS busca num raio próximo ao focus_point
        result = ors_client.pelias_search(
            text=name,
            focus_point=[focus_lon, focus_lat], # Note: ORS usa [Lon, Lat]
            size=1
        )
        if result['features']:
            # Retorna [Lon, Lat]
            return result['features'][0]['geometry']['coordinates']
    except Exception as e:
        # print(f"    (Debug ORS: {e})") # Descomente para ver erros de API
        pass
    return None

# --- A LÓGICA WATERFALL ---

def run_waterfall_strategy(row):
    raw_addr = row['Address']
    name = row['Name']
    city = row['City']
    state = row['State']

    print(f"Processando: {name} | {raw_addr} ({city}, {state})")

    # 1. TENTATIVA: Endereço Limpo (Nominatim)
    clean_addr = clean_highway_address(raw_addr)
    query_addr = f"{clean_addr}, {city}, {state}, USA"
    
    # print(f"  Trying Address: {query_addr}...")
    try:
        location = geolocator.geocode(query_addr, exactly_one=True, timeout=5)
        if location:
            print(f"  ✅ [1] ADDRESS (Nominatim): {location.latitude}, {location.longitude}")
            print(f"       Ref: {location.address[:50]}...")
            return # SUCESSO 1
    except:
        pass

    # Se falhou 1, precisamos da Cidade
    city_loc = get_city_coordinates(city, state)
    if not city_loc:
        print("  ❌ FALHA TOTAL: Nem a cidade foi encontrada.")
        return

    # 2. TENTATIVA: Nome do Posto (ORS)
    # print(f"  Trying Name POI near city...")
    poi_coords = find_poi_by_name(name, city_loc.latitude, city_loc.longitude)
    
    if poi_coords:
        # poi_coords vem como [Lon, Lat], printamos invertido pra ler fácil
        print(f"  🔵 [2] NAME POI (ORS): {poi_coords[1]}, {poi_coords[0]}") 
        return # SUCESSO 2

    # 3. TENTATIVA: Fallback Cidade
    print(f"  ⚠️ [3] CITY FALLBACK: {city_loc.latitude}, {city_loc.longitude}")
    return # SUCESSO 3


# --- DADOS DE TESTE (Casos Reais) ---
test_cases = [
    # Caso 1: Endereço Difícil, mas Nome Famoso (Woodshed) -> Esperado: ORS (Azul) ou Fallback
    {"Name": "WOODSHED OF BIG CABIN", "Address": "I-44, EXIT 283 & US-69", "City": "Big Cabin", "State": "OK"},
    
    # Caso 2: Endereço Difícil, Nome Famoso (Kwik Trip) -> Esperado: ORS (Azul)
    {"Name": "KWIK TRIP #796", "Address": "I-94, EXIT 143 & US-12 & SR-21", "City": "Tomah", "State": "WI"},
    
    # Caso 3: Endereço Simples (US-46) -> Esperado: Nominatim (Verde)
    {"Name": "ACI TRUCK STOP", "Address": "US-46", "City": "Columbia", "State": "NJ"},
    
    # Caso 4: Exit com Letra (144-B) -> Regex deve limpar -> Nominatim ou Fallback
    {"Name": "PILOT TRAVEL CENTER #123", "Address": "I-75, EXIT 144-B", "City": "Bridgeport", "State": "MI"},
]

print("--- INICIANDO TESTE WATERFALL ---\n")

for row in test_cases:
    run_waterfall_strategy(row)
    print("-" * 60)
    time.sleep(1) # Respeitar API rate limits