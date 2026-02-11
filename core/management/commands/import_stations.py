"""
import_stations: Import fuel stations from CSV with parallel geocoding.

Flow:
  1. Google Geocoding (parallel via asyncio + aiohttp)
  2. Nominatim (sequential — rate limited)
  3. ORS POI (sequential — rate limited)
  4. City fallback
"""
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus

import aiohttp
import openrouteservice
import pandas as pd
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import GoogleV3, Nominatim

from core.models import FuelStation

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class StationRow:
    """A parsed CSV row ready for geocoding."""
    opis_id: int
    name: str
    raw_addr: str
    city: str
    state: str
    price: float
    query_addr: str  # cleaned address for geocode

    # Geocoding result (populated later)
    point: Optional[Point] = field(default=None, repr=False)
    method: str = "N/A"


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def clean_highway_address(raw: str) -> str:
    """Remove EXIT, MM, AT MILE and normalize punctuation."""
    if not raw:
        return ""
    cleaned = re.sub(
        r"(?:EXIT|MM|Ex|AT\s+MILE)\s*[\w\d\-\s]+", "", raw, flags=re.IGNORECASE
    )
    cleaned = cleaned.replace("&", " and ").replace("/", " and ")
    cleaned = re.sub(r",\s*and", " and", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r",\s*,", ",", cleaned)
    return cleaned.strip(" ,")


# ---------------------------------------------------------------------------
# Async Google Geocoding (aiohttp)
# ---------------------------------------------------------------------------

GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


async def _geocode_one_google(
    row: StationRow,
    api_key: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> StationRow:
    """Geocode a single StationRow via Google REST API, respecting the semaphore."""
    async with semaphore:
        params = {"address": row.query_addr, "key": api_key}
        try:
            async with session.get(GOOGLE_GEOCODE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("status") == "OK" and data.get("results"):
                    loc = data["results"][0]["geometry"]["location"]
                    row.point = Point(loc["lng"], loc["lat"])
                    row.method = "ADDRESS (Google)"
        except Exception:
            pass  # fallback will be handled later
    return row


async def geocode_google_batch(
    rows: list[StationRow],
    api_key: str,
    concurrency: int = 10,
) -> list[StationRow]:
    """Geocode a list of StationRows in parallel using Google."""
    semaphore = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [
            _geocode_one_google(row, api_key, session, semaphore)
            for row in rows
        ]
        return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Sequential fallback (Nominatim / ORS / City)
# ---------------------------------------------------------------------------

def _geocode_nominatim(geolocator, query: str):
    """Nominatim with retry (rate limited: 1 req/s)."""
    for _ in range(2):
        try:
            return geolocator.geocode(query, timeout=5, exactly_one=True)
        except (GeocoderTimedOut, GeocoderServiceError, GeocoderUnavailable):
            time.sleep(2)
    return None


def _search_ors_poi(client, name: str, focus_coords: tuple):
    """Search POI on ORS (Pelias)."""
    try:
        result = client.pelias_search(text=name, focus_point=focus_coords, size=1)
        if result.get("features"):
            return result["features"][0]["geometry"]["coordinates"]
    except Exception:
        pass
    return None


def fallback_sequential(
    rows: list[StationRow],
    geolocator,
    ors_client,
    verbose: bool,
    log_fn=None,
) -> list[StationRow]:
    """Process rows that failed Google geocoding, one by one (rate limited)."""
    for row in rows:
        query_addr = row.query_addr
        # 1) Nominatim (address)
        loc = _geocode_nominatim(geolocator, query_addr)
        if loc:
            row.point = Point(loc.longitude, loc.latitude)
            row.method = "ADDRESS (Nominatim)"
            time.sleep(1.2)
            continue

        # 2) Nominatim (city) + ORS POI / city fallback
        query_city = f"{row.city}, {row.state}, USA"
        city_loc = _geocode_nominatim(geolocator, query_city)
        if city_loc:
            city_coords = (city_loc.longitude, city_loc.latitude)
            poi = _search_ors_poi(ors_client, row.name, city_coords) if ors_client else None
            if poi:
                row.point = Point(poi[0], poi[1])
                row.method = "NAME_POI (ORS)"
            else:
                row.point = Point(city_coords[0], city_coords[1])
                row.method = "CITY_FALLBACK"
        else:
            row.method = "FAILED"
            if log_fn:
                log_fn(f"City not found: {row.city}, {row.state}")
        time.sleep(1.2)
    return rows


# ---------------------------------------------------------------------------
# Management Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Import stations: Google (parallel) -> Nominatim -> ORS -> City"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose", action="store_true",
            help="Show errors and extra details.",
        )
        parser.add_argument(
            "--batch-size", type=int, default=200, metavar="N",
            help="Stations per bulk_create (default: 200).",
        )
        parser.add_argument(
            "--concurrency", type=int, default=10, metavar="N",
            help="Concurrent Google calls (default: 10).",
        )

    # ------------------------------------------------------------------
    # handle
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        verbose = options["verbose"]
        batch_size = max(1, options["batch_size"])
        concurrency = max(1, min(options["concurrency"], 50))

        file_path = os.path.join(settings.BASE_DIR, "fuel-prices-for-be-assessment.csv")
        google_key = getattr(settings, "GOOGLE_GEOCODE_API_KEY", "") or ""
        ors_key = getattr(settings, "ORS_API_KEY", "") or ""

        # --- Existing IDs ---
        existing_ids = set(FuelStation.objects.values_list("opis_id", flat=True))
        self.stdout.write(self.style.SUCCESS(f"Stations already in DB: {len(existing_ids)}"))

        # --- Geocoder setup ---
        if google_key:
            self.stdout.write(self.style.SUCCESS("Google Geocoding API configured."))
        else:
            self.stdout.write(self.style.WARNING("GOOGLE_GEOCODE_API_KEY not set."))

        geolocator = Nominatim(user_agent="fuel_optimizer_v2", timeout=10)

        ors_client = None
        if ors_key:
            try:
                ors_client = openrouteservice.Client(key=ors_key)
                self.stdout.write(self.style.SUCCESS("OpenRouteService configured."))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"ORS unavailable: {e}"))

        # --- Read CSV + filter ---
        try:
            df = pd.read_csv(file_path)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        rows: list[StationRow] = []
        for _, csv_row in df.iterrows():
            opis_id = int(csv_row["OPIS Truckstop ID"])
            if opis_id in existing_ids:
                continue
            existing_ids.add(opis_id)  # mark to avoid CSV duplicates
            raw_addr = str(csv_row["Address"])
            try:
                price = float(csv_row["Retail Price"])
            except (ValueError, TypeError):
                price = 0.0
            clean = clean_highway_address(raw_addr)
            city = str(csv_row["City"])
            state = str(csv_row["State"])
            rows.append(StationRow(
                opis_id=opis_id,
                name=str(csv_row["Truckstop Name"]),
                raw_addr=raw_addr,
                city=city,
                state=state,
                price=price,
                query_addr=f"{clean}, {city}, {state}, USA",
            ))

        total = len(rows)
        self.stdout.write(self.style.SUCCESS(
            f"--- Import: {total} to process (concurrency={concurrency}) ---"
        ))

        # --- Counters ---
        counts = {"Google": 0, "Nominatim": 0, "ORS": 0, "City": 0, "Failed": 0}
        saved = 0
        batch: list[FuelStation] = []

        # --- Process in chunks ---
        chunk_size = max(batch_size, concurrency * 5)
        for start in range(0, total, chunk_size):
            chunk = rows[start : start + chunk_size]

            # Phase 1: Google parallel
            if google_key:
                chunk = asyncio.run(geocode_google_batch(chunk, google_key, concurrency))

            # Separate successes and failures
            google_ok = [r for r in chunk if r.point is not None]
            google_fail = [r for r in chunk if r.point is None]

            # Phase 2: Sequential fallback
            if google_fail:
                fallback_sequential(
                    google_fail, geolocator, ors_client, verbose,
                    log_fn=lambda msg: self.stdout.write(self.style.ERROR(msg)),
                )

            # Phase 3: Save + log
            for row in chunk:
                if row.point is None:
                    counts["Failed"] += 1
                    continue

                batch.append(FuelStation(
                    opis_id=row.opis_id,
                    name=row.name,
                    address=row.raw_addr,
                    city=row.city,
                    state=row.state,
                    retail_price=row.price,
                    location=row.point,
                ))
                saved += 1

                # Counters
                if "Google" in row.method:
                    counts["Google"] += 1
                elif "Nominatim" in row.method:
                    counts["Nominatim"] += 1
                elif "ORS" in row.method:
                    counts["ORS"] += 1
                else:
                    counts["City"] += 1

                # Log
                self._log_row(saved, total, row)

                # Bulk create when batch is full
                if len(batch) >= batch_size:
                    FuelStation.objects.bulk_create(batch)
                    batch.clear()

        # Remaining
        if batch:
            FuelStation.objects.bulk_create(batch)

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- Summary ---"))
        self.stdout.write(self.style.SUCCESS(f"  Imported: {saved}"))
        self.stdout.write(
            f"  Google={counts['Google']}  Nominatim={counts['Nominatim']}"
            f"  ORS={counts['ORS']}  City={counts['City']}  Failed={counts['Failed']}"
        )
        self.stdout.write(self.style.SUCCESS(
            f"  Total in DB: {FuelStation.objects.count()}"
        ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log_row(self, n: int, total: int, row: StationRow):
        msg = f"[{n}/{total}] {row.method}: {row.name}"
        if "Google" in row.method or "ADDRESS" in row.method:
            self.stdout.write(self.style.SUCCESS(msg))
        elif "ORS" in row.method:
            self.stdout.write(self.style.MIGRATE_HEADING(msg))
        else:
            self.stdout.write(self.style.WARNING(msg))
