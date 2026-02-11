# Fuel-Optimized Route Planner API

Django/DRF API that, given start and finish locations in the USA, returns:

- Route map (GeoJSON)
- Optimal fuel stops (minimum cost via DAG algorithm)
- Total fuel cost

## Stack

| Component | Technology |
|---|---|
| Framework | Django 6 + Django REST Framework |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Containers | Docker + Docker Compose |
| Routing | OpenRouteService API |
| Geocoding | Nominatim (runtime) / Google Geocode API (ETL) |
| Optimization | DAG shortest path (DP) |
| Profiling | django-silk (DEBUG only) |

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed
- A free API key from [OpenRouteService](https://openrouteservice.org/dev/#/signup)
- (Optional) A [Google Geocoding](https://developers.google.com/maps/documentation/geocoding) API key — used only in the station import command

## Full Setup (Docker)

### 1. Configure environment variables

Create the `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
DB_NAME=routing_db
DB_USER=geouser
DB_PASSWORD=geopassword
SECRET_KEY=your-secret-key-here
ORS_API_KEY=your-ors-api-key-here
GOOGLE_GEOCODE_API_KEY=your-google-key-here   # optional
```

### 2. Start the containers

```bash
docker compose up --build -d
```

This starts two services:

| Service | Description | Port |
|---|---|---|
| `db` | PostgreSQL 16 + PostGIS 3.4 | `5432` |
| `web` | Django dev server | `8000` |

The `web` service only starts after `db` passes its health check.

### 3. Apply migrations

```bash
docker compose exec web python manage.py migrate
```

### 4. Import fuel stations

```bash
docker compose exec web python manage.py import_stations fuel-prices-for-be-assessment.csv
```

> The command geocodes ~8000 stations using Google API (parallel via asyncio) with Nominatim fallback. Use `--concurrency 20` to adjust parallelism.

### 5. Verify it's running

```bash
# Live logs
docker compose logs -f web

# Container status
docker compose ps
```

Open `http://localhost:8000/api/route/?start=Los+Angeles,CA&end=New+York,NY` in your browser.

## API Usage

### GET (query params)

```bash
curl "http://localhost:8000/api/route/?start=Los+Angeles,CA&end=New+York,NY"
```

### POST (JSON body)

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "33.94,-118.41", "end": "40.78,-73.97"}'
```

### Parameters

| Field | Type | Description |
|---|---|---|
| `start` | string | Address, city, or `"lat,lon"` |
| `end` | string | Address, city, or `"lat,lon"` |

### Example response

```json
{
  "route_geojson": {
    "type": "Feature",
    "geometry": { "type": "LineString", "coordinates": [...] },
    "properties": {}
  },
  "stops": [
    {
      "mileage": 312.5,
      "lat": 35.19,
      "lon": -111.65,
      "name": "PILOT",
      "address": "5101 Intermodal Dr",
      "price": 3.219,
      "gallons": 31.25,
      "cost": 100.59
    }
  ],
  "total_fuel_cost": 725.30,
  "total_gallons": 278.5,
  "total_miles": 2785.0,
  "mpg_used": 10
}
```

## Tests

```bash
docker compose exec web python manage.py test core.tests -v 2
```

Run a single test module:

```bash
# API tests only
docker compose exec web python manage.py test core.tests.test_apis -v 2

# DAG algorithm tests only
docker compose exec web python manage.py test core.tests.test_logic -v 2

# Service tests only
docker compose exec web python manage.py test core.tests.test_services -v 2

# Selector tests only
docker compose exec web python manage.py test core.tests.test_selectors -v 2
```

## Profiling (django-silk)

Silk is automatically enabled when `DEBUG=True`. Access it at:

```
http://localhost:8000/silk/
```

## Useful Docker Commands

```bash
# Start in foreground (see logs directly)
docker compose up --build

# Stop everything
docker compose down

# Stop and remove volumes (deletes the database!)
docker compose down -v

# Open Django shell in the container
docker compose exec web python manage.py shell

# Open psql on the database
docker compose exec db psql -U geouser -d routing_db

# Rebuild web only (after changing requirements.txt)
docker compose build web && docker compose up -d web
```

## Architecture (HackSoft Django Styleguide)

```
core/
├── constants.py      # Centralized magic numbers
├── exceptions.py     # Custom DRF exception handler
├── logic.py          # DAG algorithm (pure Python)
├── models.py         # BaseModel + FuelStation
├── selectors.py      # Database reads (queries)
├── services.py       # Business logic + external integrations
├── views.py          # Thin views (validate → call service → serialize)
├── urls.py
├── management/
│   └── commands/
│       └── import_stations.py   # ETL: CSV → geocode → PostGIS
└── tests/
    ├── test_apis.py       # API tests (RoutePlanApi)
    ├── test_logic.py      # DAG algorithm tests
    ├── test_selectors.py  # Selector tests
    └── test_services.py   # Service tests
```

**Principles:**
- **Services** (write/logic) and **Selectors** (read) — never in views or serializers
- **Thin views** — validate input, call service, serialize output
- **Type hints** throughout the codebase
- **Centralized constants** in `constants.py`
- **Custom exception handler** for consistent error responses
