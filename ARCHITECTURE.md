# Architecture and Detailed Walkthrough

Technical document describing in depth how each part of the system works, from the HTTP request to the final response with optimized fuel stops.

---

## High-Level Flow

When a user makes a request to `/api/route/?start=A&end=B`, the system executes the following steps:

```
HTTP Request
       │
       ▼
  ┌─────────────┐
  │   View      │  Validates input (InputSerializer)
  │ RoutePlanApi│  Calls service
  └──────┬──────┘
         │
         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Service: route_plan(start, end)                        │
  │                                                         │
  │  1. geocode_to_coords(start)  →  (lat, lon)            │
  │  2. geocode_to_coords(end)    →  (lat, lon)            │
  │  3. get_route(start, end)     →  (LineString, miles)    │
  │  4. station_list_on_route()   →  QuerySet [Selector]    │
  │  5. _build_station_nodes()    →  list[RouteNode]        │
  │  6. prefilter_stations()      →  list[RouteNode]        │
  │  7. optimize_refuel_dag()     →  stops, cost, gallons   │
  │  8. Builds response dict                                │
  └─────────────────────────────────────────────────────────┘
         │
         ▼
  ┌─────────────┐
  │   View      │  Serializes output (OutputSerializer)
  │ RoutePlanApi│  Returns JSON Response
  └─────────────┘
```

---

## 1. Entry Point: View (`core/views.py`)

`RoutePlanApi` follows the **Thin View** pattern from the HackSoft Django Styleguide. It has exactly 3 responsibilities:

1. **Validate input** — via `InputSerializer` (fields `start` and `end`, both required)
2. **Call the service** — `route_plan(start=..., end=...)`
3. **Serialize output** — via `OutputSerializer`

```python
def _handle(self, request):
    input_ser = self.InputSerializer(data=...)
    input_ser.is_valid(raise_exception=True)

    result = route_plan(**input_ser.validated_data)  # all logic lives here

    output_ser = self.OutputSerializer(result)
    return Response(output_ser.data)
```

The view accepts both **GET** (query params) and **POST** (JSON body). If the `InputSerializer` fails validation, DRF returns 400 automatically. If the service raises `ValueError`, the view catches it and returns 400 with the error message.

Serializers are **nested inside the view** (inner classes), following the styleguide convention of not creating a separate `serializers.py` file when serializers are specific to a single view.

---

## 2. Geocoding (`core/services.py` — `geocode_to_coords`)

Converts the user's input string into `(lat, lon)` coordinates.

### Resolution Flow

```
Input: "Los Angeles, CA"  or  "33.94,-118.41"
                │
                ▼
       ┌────────────────┐
       │ Is "lat,lon" ? │──── Yes ──→ Validate bounds (-90≤lat≤90, -180≤lon≤180)
       │ (regex match)  │                          │
       └───────┬────────┘                     Return (lat, lon)
               │ No
               ▼
       ┌────────────────┐
       │  Nominatim     │──── Found? ──→ Return (lat, lon)
       │ (OpenStreetMap) │
       └───────┬────────┘
               │ No
               ▼
          Return None  →  ValueError("Could not resolve...")
```

**Technical details:**

- The regex `^(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)$` accepts formats like `"40.7,-74.0"` or `"-33.5, 150.2"`
- The Nominatim geocoder is instantiated as a singleton (`_get_geolocator()`) to reuse the connection
- Nominatim timeout/service errors are silenced (returns `None` → `ValueError`)

---

## 3. ORS Routing (`core/services.py` — `get_route`)

Retrieves the route geometry and total distance by calling the OpenRouteService API.

### Flow

```
Input: start_coords (lon, lat),  end_coords (lon, lat)
                │
                ▼
      ┌──────────────────┐
      │  Cache hit?      │──── Yes ──→ Return (LineString, miles)  [~0ms]
      │  (LocMemCache)   │
      └────────┬─────────┘
               │ No
               ▼
      ┌──────────────────┐
      │  POST to ORS     │  Persistent session (reuses TCP/SSL)
      │  /driving-car/   │  Timeout: 30s
      │  geojson         │
      └────────┬─────────┘
               │
               ▼
      Parse GeoJSON response:
        - coordinates → LineString(coords, srid=4326)
        - summary.distance (meters) × 0.000621371 → miles
               │
               ▼
      Save to cache (TTL: 1 hour)
      Return (LineString, total_miles)
```

**Performance optimizations:**

1. **`requests.Session`** — The `_get_http_session()` singleton keeps the TCP/SSL connection open between calls. This eliminates the SSL handshake (~2s) on subsequent calls
2. **`LocMemCache`** — Identical routes are cached for 1 hour. The key is an MD5 of the coordinates with 6 decimal places
3. The API is called **only once** per request (ideal per spec)

**ORS coordinate format:**

ORS expects coordinates as `(lon, lat)` (opposite of the standard `(lat, lon)`). The conversion is done in the `route_plan` service:

```python
start_coords = (start_ll[1], start_ll[0])  # (lat,lon) → (lon,lat)
```

---

## 4. Spatial Station Search (`core/selectors.py` — `station_list_on_route`)

Finds all fuel stations near the route using **PostGIS spatial queries**.

### How it works

```sql
-- Pseudo-SQL generated by the Django ORM:
SELECT opis_id, name, address, retail_price, location,
       ST_LineLocatePoint(route_geom, location) AS fraction
FROM fuel_station
WHERE ST_DWithin(location, route_geom, 0.1449)  -- ~10 miles in degrees
ORDER BY fraction;
```

**Details:**

- **`dwithin`**: Uses the PostGIS GiST spatial index to find points within a buffer of the route. More efficient than `buffer()` + `within` because it leverages the spatial index directly
- **10-mile buffer**: `STATION_BUFFER_MI × DEGREES_PER_MILE = 10 × (1/69) ≈ 0.1449°`. Since we use SRID 4326 (degrees), the conversion is approximate (1° lat ≈ 69 mi)
- **`LineLocatePoint`**: PostGIS function that returns the fraction (0.0 to 1.0) of where a point projects onto a line. If the route is 1000 miles and the station is at 0.3, it's at mile 300
- **`.only()`**: Optimization — loads only the required fields from the database
- **Ordering by `fraction`**: Ensures stations are in the correct order along the route

---

## 5. Building RouteNodes (`core/services.py` — `_build_station_nodes`)

Converts the `FuelStation` QuerySet into a list of `RouteNode` (TypedDict).

```python
RouteNode = {
    "mileage": 300.0,      # position in miles = fraction × total_miles
    "price": 3.219,        # price per gallon (float from DecimalField)
    "lat": 35.19,          # latitude
    "lon": -111.65,        # longitude
    "name": "PILOT",       # station name
    "address": "5101...",  # address
    "station_id": 12345,   # original opis_id from CSV
}
```

The mileage is calculated as `fraction × total_miles`, converting the relative position (0.0-1.0) to absolute miles along the route.

---

## 6. Pre-filtering (`core/services.py` — `prefilter_stations`)

Reduces the number of DAG nodes by eliminating micro-stops.

### Problem

On a 2800-mile route, PostGIS may find hundreds of stations. Many are only a few miles apart (e.g., 3 stations at the same highway exit). Without filtering, the DAG would have hundreds of nodes and the result might suggest stops 3 miles apart.

### Solution

Divides the route into **50-mile segments** and keeps only **the cheapest station** per segment.

```
Route: 0 ──────────── 50 ──────────── 100 ──────────── 150 ...
          Segment 0       Segment 1        Segment 2

Stations in segment 0:
  - Shell @ mile 10, $3.50
  - BP @ mile 12, $3.20    ← cheapest, kept
  - Exxon @ mile 15, $3.45

Result: only BP @ mile 12 passes to the DAG
```

**Algorithm:**

1. Compute `bucket_id = int(mileage / 50)` for each station
2. Group into buckets by `bucket_id`
3. From each bucket, select `min(price)`
4. Return list sorted by `bucket_id`

This typically reduces ~200 stations to ~50 nodes, without significant loss in optimization quality.

---

## 7. Minimum-Cost DAG Algorithm (`core/logic.py` — `optimize_refuel_dag`)

The heart of the system. Finds the **global optimum** for fuel cost, not just a greedy heuristic.

### Graph Modeling

```
Nodes:   [Start] → [Station1] → [Station2] → ... → [StationN] → [Finish]
         mile 0     mile 120      mile 350           mile 2600     mile 2800

Edges: i → j exists if mileage(j) - mileage(i) ≤ 500 (vehicle range)

Edge weight i→j:
    gallons = (mileage(j) - mileage(i)) / 10 MPG
    cost    = gallons × price(i)
    (fuel is purchased at station i to reach station j)
```

### Why DAG and not Dijkstra?

The graph is a **DAG** (Directed Acyclic Graph) because:
- Nodes are ordered by mileage
- Edges always go from lower to higher mileage (never backwards)
- There are no cycles

In DAGs, the **shortest path** can be solved in **O(V + E)** with dynamic programming, more efficient than Dijkstra's O((V+E) log V).

### Algorithm (DP)

```python
# Initialization
min_cost[0] = 0.0      # Start has zero cost
min_cost[1..n] = ∞     # all others are infinity
parent[0..n] = None     # for path reconstruction

# Relaxation (topological order = index order)
for i in 0..n:
    if min_cost[i] == ∞: continue     # unreachable node
    for j in i+1..n:
        dist = mileage[j] - mileage[i]
        if dist > 500: break           # out of range → all following are too
        cost_ij = (dist / 10) × price[i]
        if min_cost[i] + cost_ij < min_cost[j]:
            min_cost[j] = min_cost[i] + cost_ij
            parent[j] = i              # record best predecessor
```

### Path Reconstruction

```python
# Traverse parent[] backwards
path = [Finish]
while parent[current] is not None:
    path.prepend(parent[current])
    current = parent[current]
# path = [Start, Station_A, Station_C, ..., Finish]
```

### Computing gallons and cost per stop

For each consecutive pair `(i, j)` in the optimal path:

```
gallons_purchased_at_i = (mileage[j] - mileage[i]) / 10
cost_at_i = gallons_purchased_at_i × price[i]
```

**Note:** Start (index 0) has `price=0` — fuel between Start and the first stop has zero cost (assumed full tank at departure).

### Infeasible case

If `min_cost[Finish] == ∞`, no viable path exists (some segment > 500 mi without a station). The system returns `total_cost = None` and the service raises `ValueError`.

### Numerical precision

Intermediate calculations use `float` for performance, but final totals (`total_cost`, `total_gallons`) are computed with `Decimal` to avoid rounding errors in long sums.

---

## 8. Final Response

The `route_plan` service builds the final dictionary:

```python
{
    "route_geojson": {                    # GeoJSON Feature of the full route
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat], ...]  # thousands of points
        },
        "properties": {}
    },
    "stops": [                            # only the stops chosen by the DAG
        {
            "mileage": 312.5,             # position on route (miles)
            "lat": 35.19,
            "lon": -111.65,
            "name": "PILOT",
            "address": "5101 Intermodal Dr",
            "price": 3.219,               # price per gallon
            "gallons": 31.25,             # gallons purchased here
            "cost": 100.59                # cost at this stop
        }
    ],
    "total_fuel_cost": 725.30,            # sum of all cost
    "total_gallons": 278.5,               # sum of all gallons
    "total_miles": 2785.0,                # total route distance
    "mpg_used": 10                        # fuel economy used in calculation
}
```

The view serializes it with `OutputSerializer` and returns it as JSON.

---

## 9. ETL: Station Import (`core/management/commands/import_stations.py`)

The `import_stations` command loads ~8000 stations from a CSV into the database with geocoding.

### Flow

```
CSV (8000 rows)
       │
       ▼
  Filter out already-existing stations (by opis_id)
       │
       ▼
  ┌──────────────────────────────────────────────┐
  │ Phase 1: Google Geocoding (parallel)         │
  │   • asyncio + aiohttp                        │
  │   • Semaphore (default 10 concurrent calls)  │
  │   • ~95% success rate                        │
  └──────────────────────────────────────────────┘
       │
       ▼ (stations that failed Google)
  ┌──────────────────────────────────────────────┐
  │ Phase 2: Sequential fallback                 │
  │   1. Nominatim (address) — rate: 1 req/s     │
  │   2. Nominatim (city) + ORS POI (Pelias)     │
  │   3. City fallback (city coordinates)        │
  └──────────────────────────────────────────────┘
       │
       ▼
  bulk_create (batches of 200)
```

### Google Parallelism

```python
async def geocode_google_batch(rows, api_key, concurrency=10):
    semaphore = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        tasks = [_geocode_one_google(row, api_key, session, semaphore) for row in rows]
        return await asyncio.gather(*tasks)
```

- **`asyncio.Semaphore`**: Limits concurrent calls (prevents Google throttling)
- **`aiohttp.ClientSession`**: A single shared HTTP session (connection pool)
- **`asyncio.gather`**: Runs all tasks in parallel, collecting results

### Address Cleaning

Truck stop addresses often contain highway information that interferes with geocoding:

```
Before: "EXIT 286 AT MILE 286 I-40, 5101 Intermodal Dr"
After:  "5101 Intermodal Dr"
```

The `clean_highway_address` function strips patterns like `EXIT`, `MM`, `AT MILE` via regex.

---

## 10. Data Model (`core/models.py`)

```
┌──────────────────────────────────────────────────┐
│                 FuelStation                       │
├──────────────────────────────────────────────────┤
│ opis_id       INTEGER  UNIQUE        (CSV ID)    │
│ name          VARCHAR(255)                       │
│ address       VARCHAR(255)                       │
│ city          VARCHAR(100)                       │
│ state         VARCHAR(20)                        │
│ retail_price  DECIMAL(10,3) INDEXED              │
│ location      POINT(4326)   SPATIAL INDEX (GiST) │
│ created_at    TIMESTAMP     INDEXED              │
│ updated_at    TIMESTAMP     AUTO                 │
└──────────────────────────────────────────────────┘
```

- **SRID 4326**: WGS84 coordinates (lat/lon in degrees) — worldwide GPS standard
- **Spatial Index (GiST)**: PostGIS spatial index for fast geographic searches (`dwithin`)
- **`retail_price` indexed**: Enables efficient price-based sorting
- **`BaseModel`**: Abstract class with `created_at`/`updated_at` (HackSoft pattern)

---

## 11. Caching and Performance

### Cache Layer

```
┌─────────────┐     ┌─────────────┐     ┌──────────┐
│   Request   │────→│ LocMemCache │────→│  ORS API │
│             │     │ (1h TTL)    │     │ (~2-5s)  │
│             │◄────│             │◄────│          │
└─────────────┘     └─────────────┘     └──────────┘
```

- **1st call**: ~3-5s (includes SSL handshake + ORS call)
- **2nd call (same route)**: ~0ms (cache hit)
- **Different route, same session**: ~1-2s (SSL already established via Session)

### Typical Time Breakdown (long route, cold start)

| Step | Time |
|---|---|
| Geocoding (2× Nominatim) | ~1s |
| ORS API (with SSL handshake) | ~3-5s |
| PostGIS dwithin + LineLocatePoint | ~50ms |
| Prefilter + DAG | ~5ms |
| JSON serialization | ~10ms |
| **Total** | **~4-6s** |

---

## 12. Constants (`core/constants.py`)

All magic numbers are centralized:

| Constant | Value | Usage |
|---|---|---|
| `VEHICLE_RANGE_MI` | 500 | Maximum vehicle range |
| `VEHICLE_MPG` | 10 | Fuel economy (miles per gallon) |
| `STATION_BUFFER_MI` | 10 | Search radius for stations around the route |
| `DEGREES_PER_MILE` | 1/69 | Degrees to miles conversion (approx.) |
| `PREFILTER_SEGMENT_MI` | 50 | Segment size for pre-filtering |
| `METERS_TO_MILES` | 0.000621371 | Meters to miles conversion |
| `ORS_ROUTE_URL` | `https://...` | ORS API endpoint |
| `ROUTE_CACHE_TTL` | 3600 | Cache TTL (1 hour) |

---

## 13. Exception Handling (`core/exceptions.py`)

Custom handler registered via `REST_FRAMEWORK["EXCEPTION_HANDLER"]`.

**What it does:**

1. Converts `Django ValidationError` → `DRF ValidationError` (for consistent JSON format)
2. Ensures errors with `detail` as list/dict are wrapped in `{"detail": ...}`

This prevents Django from returning HTML errors instead of JSON when a Django `ValidationError` (not DRF) is raised inside a service.

---

## 14. Test Organization

```
core/tests/
├── test_apis.py        # Tests the view by mocking the service (route_plan)
├── test_logic.py       # Tests the DAG with pure scenarios (no DB, no mocks)
├── test_selectors.py   # Tests PostGIS queries (requires test database)
└── test_services.py    # Tests geocode, get_route (mocked HTTP), prefilter
```

### Mocking Strategy

- **`test_apis.py`**: Mocks `core.views.route_plan` — tests only the view (serialization, validation, HTTP status codes)
- **`test_services.py`**: Mocks `core.services._get_http_session` and `core.services._get_geolocator` — tests service logic without calling external APIs
- **`test_logic.py`**: No mocks — tests the DAG algorithm with manually constructed data
- **`test_selectors.py`**: Uses Django's test database — verifies PostGIS queries run without error

---

## 15. Module Dependency Diagram

```
views.py
   │
   └──→ services.py
            │
            ├──→ selectors.py ──→ models.py
            │                        │
            ├──→ logic.py            └──→ constants.py
            │      │
            │      └──→ constants.py
            │
            └──→ constants.py

exceptions.py  (registered via settings.py, used by DRF)
```

**Rule: dependencies flow top-down. Views never import models directly. Logic never imports services. Selectors never import services.**
