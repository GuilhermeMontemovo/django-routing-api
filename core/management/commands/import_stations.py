import os
import time
import re
import pandas as pd
import openrouteservice
from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Point
from core.models import FuelStation
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError, GeocoderUnavailable

class Command(BaseCommand):
    help = 'Importa postos: Endereço (Nominatim) -> Nome (ORS) -> Cidade (Fallback)'

    def handle(self, *args, **options):
        # Caminho do CSV na raiz do projeto (funciona local e no Docker: /app no container)
        file_path = os.path.join(settings.BASE_DIR, 'fuel-prices-for-be-assessment.csv')
        
        # --- CONFIGURAÇÃO ---
        # 1. Nominatim (Endereços e Cidades)
        # User-agent é obrigatório para evitar bloqueio
        geolocator = Nominatim(user_agent="fuel_optimizer_prod_v1", timeout=10)
        
        # 2. OpenRouteService (Busca por Nome/POI)
        # Tenta pegar do settings.py, se não tiver, usa string vazia (o que desativa a etapa 2)
        ORS_KEY = getattr(settings, 'ORS_API_KEY', '')
        ors_client = None
        
        if ORS_KEY:
            try:
                ors_client = openrouteservice.Client(key=ORS_KEY)
                self.stdout.write(self.style.SUCCESS("API OpenRouteService configurada com sucesso."))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Erro ao configurar ORS: {e}. A busca por POI será ignorada."))
        else:
            self.stdout.write(self.style.WARNING("Chave ORS_API_KEY não encontrada. A busca por POI será ignorada."))

        try:
            df = pd.read_csv(file_path)
            # DICA: Para teste rápido, descomente a linha abaixo para importar apenas 20 linhas:
            # df = df.head(20) 
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"Arquivo não encontrado: {file_path}"))
            return

        total = len(df)
        self.stdout.write(self.style.SUCCESS(f"--- Iniciando Importação Waterfall ({total} registros) ---"))

        success_count = 0
        
        for index, row in df.iterrows():
            opis_id = row['OPIS Truckstop ID']
            
            # Idempotência: Se já existe, pula
            if FuelStation.objects.filter(opis_id=opis_id).exists():
                # self.stdout.write(f"ID {opis_id} já existe. Pulando.")
                continue

            # Dados Brutos
            raw_addr = str(row['Address'])
            name = str(row['Truckstop Name'])
            city = str(row['City'])
            state = str(row['State'])
            
            try:
                price = float(row['Retail Price'])
            except:
                price = 0.0

            # --- A LÓGICA WATERFALL ---
            point = None
            method = "N/A"

            # 1. TENTATIVA: Endereço Limpo (Nominatim)
            # Remove "EXIT", "MM", etc.
            clean_addr = self.clean_highway_address(raw_addr)
            query_addr = f"{clean_addr}, {city}, {state}, USA"
            
            location = self.geocode_nominatim(geolocator, query_addr)

            if location:
                point = Point(location.longitude, location.latitude)
                method = "ADDRESS (Nominatim)"
            
            else:
                # Se falhou endereço, precisamos da coordenada da CIDADE para os próximos passos
                query_city = f"{city}, {state}, USA"
                city_loc = self.geocode_nominatim(geolocator, query_city)

                if city_loc:
                    city_coords = (city_loc.longitude, city_loc.latitude) # (Lon, Lat)
                    
                    # 2. TENTATIVA: Nome do Posto (ORS POI Search)
                    # Só tenta se o cliente ORS estiver configurado
                    poi_coords = None
                    if ors_client:
                        poi_coords = self.search_ors_poi(ors_client, name, city_coords)
                    
                    if poi_coords:
                        point = Point(poi_coords[0], poi_coords[1])
                        method = "NAME_POI (ORS)"
                    else:
                        # 3. TENTATIVA: Fallback Cidade
                        point = Point(city_coords[0], city_coords[1])
                        method = "CITY_FALLBACK"
                else:
                    self.stdout.write(self.style.ERROR(f"[{index+1}] ❌ FALHA CRÍTICA: Cidade não encontrada {city}"))
                    continue

            # --- SALVAR NO BANCO ---
            if point:
                try:
                    FuelStation.objects.create(
                        opis_id=opis_id,
                        name=name,
                        address=raw_addr,
                        city=city,
                        state=state,
                        retail_price=price,
                        location=point
                    )
                    success_count += 1
                    
                    # Feedback Visual Colorido
                    log_msg = f"[{index+1}/{total}] {method}: {name}"
                    if "ADDRESS" in method:
                        self.stdout.write(self.style.SUCCESS(log_msg)) # Verde
                    elif "NAME" in method:
                        self.stdout.write(self.style.MIGRATE_HEADING(log_msg)) # Ciano
                    else:
                        self.stdout.write(self.style.WARNING(log_msg)) # Amarelo (Fallback)

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Erro ao salvar DB: {e}"))

            # Rate Limit (1.2s é seguro para Nominatim e ORS Free)
            time.sleep(1.2)
            
        self.stdout.write(self.style.SUCCESS(f"\nImportação concluída! Total importado: {success_count}"))

    def clean_highway_address(self, raw_address):
        """ Limpeza Regex Otimizada """
        if not raw_address: return ""
        
        # Remove EXIT, MM, AT MILE e números/letras seguintes
        cleaned = re.sub(r'(?:EXIT|MM|Ex|AT\s+MILE)\s*[\w\d\-\s]+', '', raw_address, flags=re.IGNORECASE)
        
        # Padroniza 'and'
        cleaned = cleaned.replace('&', ' and ').replace('/', ' and ')
        
        # Remove vírgula antes do 'and' (Crucial para geocoders)
        cleaned = re.sub(r',\s*and', ' and', cleaned, flags=re.IGNORECASE)

        # Limpeza final de pontuação
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        cleaned = re.sub(r',\s*,', ',', cleaned)
        cleaned = cleaned.strip(' ,')
        
        return cleaned

    def geocode_nominatim(self, geolocator, query):
        """ Wrapper com Retry para Nominatim """
        for attempt in range(2): # Tenta 2 vezes em caso de timeout
            try:
                return geolocator.geocode(query, timeout=5, exactly_one=True)
            except (GeocoderTimedOut, GeocoderServiceError, GeocoderUnavailable):
                time.sleep(2)
        return None

    def search_ors_poi(self, client, name, focus_coords):
        """ 
        Busca o nome do posto usando OpenRouteService (Pelias)
        focus_coords: tupla (lon, lat)
        """
        try:
            # Removemos boundary_country que causava erro na lib Python
            # focus_point já restringe geograficamente a busca
            result = client.pelias_search(
                text=name,
                focus_point=focus_coords, 
                size=1
            )
            
            if result['features']:
                # Retorna [lon, lat]
                return result['features'][0]['geometry']['coordinates']
        except Exception:
            # Falha silenciosa para cair no fallback
            pass
        return None