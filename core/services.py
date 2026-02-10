import requests
import polyline
from django.conf import settings
from django.contrib.gis.geos import LineString
from django.contrib.gis.measure import D
from django.contrib.gis.db.models.functions import LineLocatePoint
from .models import FuelStation

def get_route(start_coords, end_coords):
    """
    start_coords: tupla (lon, lat)
    end_coords: tupla (lon, lat)
    Retorna: geometria LineString e distância total em milhas
    """
    # Exemplo de chamada ao ORS
    headers = {'Authorization': settings.ORS_API_KEY}
    body = {"coordinates": [start_coords, end_coords]}
    
    # Endpoint correto para driving-car
    url = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    
    response = requests.post(url, json=body, headers=headers)
    data = response.json()
    
    # Extrai geometria e converte para objeto GEOS
    # O ORS retorna GeoJSON, que já é compatível
    coords = data['features'][0]['geometry']['coordinates']
    route_geom = LineString(coords, srid=4326)
    
    # Distância total (ORS retorna em metros)
    dist_meters = data['features'][0]['properties']['summary']['distance']
    total_miles = dist_meters * 0.000621371
    
    return route_geom, total_miles

def find_stations_on_route(route_geom):
    """
    Encontra postos num buffer de 10 milhas e calcula a posição linear (0.0 a 1.0)
    """
    return FuelStation.objects.filter(
        location__dwithin=(route_geom, D(mi=10)) # Buffer espacial
    ).annotate(
        fraction=LineLocatePoint(route_geom, 'location') # Projeção linear
    ).order_by('fraction') # Ordena do início ao fim da rota