"""
Services — business logic and external API integrations.

Following the HackSoft Django Styleguide:
  - Services encapsulate write / business logic
  - Keyword-only args for the public interface
  - Type hints everywhere
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import Any

import requests
from django.conf import settings
from django.contrib.gis.geos import LineString
from django.core.cache import cache
from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from core.constants import (
    METERS_TO_MILES,
    ORS_ROUTE_URL,
    PREFILTER_SEGMENT_MI,
    ROUTE_CACHE_TTL,
    VEHICLE_MPG,
    VEHICLE_RANGE_MI,
)
from core.logic import RouteNode, optimize_refuel_dag
from core.selectors import station_list_on_route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reusable singletons (keep SSL connection open between requests)
# ---------------------------------------------------------------------------
_geolocator: Nominatim | None = None
_http_session: requests.Session | None = None


def _get_geolocator() -> Nominatim:
    global _geolocator
    if _geolocator is None:
        _geolocator = Nominatim(user_agent="fuel_optimizer_route_v1", timeout=10)
    return _geolocator


def _get_http_session() -> requests.Session:
    """Persistent requests Session — reuses TCP/SSL connection with ORS."""
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(r"^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$")


def geocode_to_coords(place: str) -> tuple[float, float] | None:
    """
    Resolve a place string to ``(lat, lon)`` or ``None``.

    Accepts ``"lat,lon"`` or a textual address (Nominatim).
    """
    if not place or not str(place).strip():
        return None

    s = str(place).strip()

    match = _COORD_RE.match(s)
    if match:
        try:
            lat, lon = float(match.group(1)), float(match.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
            return None  # coordinates out of bounds
        except ValueError:
            pass

    try:
        loc = _get_geolocator().geocode(s, exactly_one=True, timeout=10)
        if loc:
            return (loc.latitude, loc.longitude)
    except (GeocoderTimedOut, GeocoderServiceError, GeocoderUnavailable):
        pass

    return None


# ---------------------------------------------------------------------------
# Routing (ORS) — with persistent Session + cache
# ---------------------------------------------------------------------------


def _route_cache_key(
    start_coords: tuple[float, float],
    end_coords: tuple[float, float],
) -> str:
    """Generate a deterministic cache key for a coordinate pair."""
    raw = (
        f"{start_coords[0]:.6f},{start_coords[1]:.6f}"
        f"|{end_coords[0]:.6f},{end_coords[1]:.6f}"
    )
    return f"ors_route:{hashlib.md5(raw.encode()).hexdigest()}"


def get_route(
    start_coords: tuple[float, float],
    end_coords: tuple[float, float],
) -> tuple[LineString, float]:
    """
    Call ORS and return ``(route_geom, total_miles)``.

    ``start_coords`` / ``end_coords`` in ORS format ``(lon, lat)``.
    Raises ``ValueError`` on API error or invalid response.
    """
    cache_key = _route_cache_key(start_coords, end_coords)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("[ROUTE] cache hit for %s -> %s", start_coords, end_coords)
        return cached

    session = _get_http_session()
    headers = {"Authorization": settings.ORS_API_KEY}
    body: dict[str, Any] = {
        "coordinates": [list(start_coords), list(end_coords)],
    }
    response = session.post(ORS_ROUTE_URL, json=body, headers=headers, timeout=30)
    data: dict[str, Any] = response.json()

    if response.status_code != 200:
        msg = data.get("error", {}).get("message", response.text) or "Route not found"
        raise ValueError(msg)

    if not data.get("features"):
        raise ValueError("No route returned")

    coords = data["features"][0]["geometry"]["coordinates"]
    route_geom = LineString(coords, srid=4326)
    dist_meters: float = data["features"][0]["properties"]["summary"]["distance"]
    total_miles = dist_meters * METERS_TO_MILES

    result = (route_geom, total_miles)
    cache.set(cache_key, result, ROUTE_CACHE_TTL)
    logger.info(
        "[ROUTE] cached %s -> %s (%.0f mi)", start_coords, end_coords, total_miles
    )

    return result


# ---------------------------------------------------------------------------
# Pre-filter: keep only the cheapest station per N-mile segment
# ---------------------------------------------------------------------------


def prefilter_stations(
    station_nodes: list[RouteNode],
    segment_miles: int = PREFILTER_SEGMENT_MI,
) -> list[RouteNode]:
    """
    Group stations into ``segment_miles``-long segments along the route
    and return only the cheapest station per segment.

    Eliminates micro-stops without significantly affecting total cost,
    and reduces the number of DAG nodes.
    """
    if not station_nodes:
        return []

    buckets: dict[int, list[RouteNode]] = defaultdict(list)
    for node in station_nodes:
        bucket_id = int(node["mileage"] / segment_miles)
        buckets[bucket_id].append(node)

    filtered: list[RouteNode] = []
    for bucket_id in sorted(buckets):
        cheapest = min(buckets[bucket_id], key=lambda n: n["price"])
        filtered.append(cheapest)

    return filtered


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_station_nodes(
    stations_qs,
    total_miles: float,
) -> list[RouteNode]:
    """Convert a FuelStation queryset annotated with ``fraction`` into RouteNodes."""
    nodes: list[RouteNode] = []
    for s in stations_qs:
        fraction = getattr(s, "fraction", None)
        if fraction is None:
            continue
        mileage = float(fraction) * total_miles
        pt = s.location
        lat: float = pt.y if hasattr(pt, "y") else pt.coords[1]
        lon: float = pt.x if hasattr(pt, "x") else pt.coords[0]
        nodes.append(
            RouteNode(
                mileage=mileage,
                price=float(s.retail_price),
                lat=lat,
                lon=lon,
                name=s.name,
                address=s.address or "",
                station_id=s.opis_id,
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# Main service — orchestrates the full flow
# ---------------------------------------------------------------------------


def route_plan(*, start: str, end: str) -> dict[str, Any]:
    """
    Orchestrate: geocode -> ORS route -> find stations -> prefilter -> DAG.

    Raises ``ValueError`` if input is invalid or the route is impossible.
    """
    start_ll = geocode_to_coords(start)
    if not start_ll:
        raise ValueError("Could not resolve start location.")

    end_ll = geocode_to_coords(end)
    if not end_ll:
        raise ValueError("Could not resolve end location.")

    # ORS uses (lon, lat)
    start_coords = (start_ll[1], start_ll[0])
    end_coords = (end_ll[1], end_ll[0])

    route_geom, total_miles = get_route(start_coords, end_coords)

    # Find stations along the route (selector)
    stations_qs = station_list_on_route(route_geom=route_geom)
    station_nodes = _build_station_nodes(stations_qs, total_miles)
    station_nodes = prefilter_stations(station_nodes)

    # Build full node list: Start + stations + Finish
    start_node = RouteNode(
        mileage=0.0,
        price=0.0,
        lat=start_ll[0],
        lon=start_ll[1],
        name="Start",
        address="",
        station_id=None,
    )
    finish_node = RouteNode(
        mileage=total_miles,
        price=0.0,
        lat=end_ll[0],
        lon=end_ll[1],
        name="Finish",
        address="",
        station_id=None,
    )
    nodes = [start_node] + station_nodes + [finish_node]

    path_stops, total_cost, total_gallons = optimize_refuel_dag(
        nodes, total_miles, range_mi=VEHICLE_RANGE_MI, mpg=VEHICLE_MPG
    )

    if total_cost is None:
        raise ValueError(
            "No feasible refuel path (e.g. segment > 500 miles without a station)."
        )

    # Build route GeoJSON
    coords = [[c[0], c[1]] for c in route_geom.coords]
    route_geojson: dict[str, Any] = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {},
    }

    return {
        "route_geojson": route_geojson,
        "stops": path_stops,
        "total_fuel_cost": float(total_cost),
        "total_gallons": float(total_gallons),
        "total_miles": total_miles,
        "mpg_used": VEHICLE_MPG,
    }
