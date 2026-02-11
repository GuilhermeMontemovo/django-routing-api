from unittest.mock import MagicMock, patch

from django.contrib.gis.geos import LineString
from django.test import TestCase

from core.services import geocode_to_coords, get_route, prefilter_stations


# ---------------------------------------------------------------------------
# geocode_to_coords
# ---------------------------------------------------------------------------


class GeocodeToCoordsTestCase(TestCase):
    """Tests for geocode_to_coords."""

    def test_empty_returns_none(self):
        self.assertIsNone(geocode_to_coords(""))
        self.assertIsNone(geocode_to_coords(None))

    def test_lat_lon_string_parsed(self):
        result = geocode_to_coords("40.7, -74.0")
        self.assertIsNotNone(result)
        lat, lon = result
        self.assertAlmostEqual(lat, 40.7, places=5)
        self.assertAlmostEqual(lon, -74.0, places=5)

    def test_lat_lon_no_space(self):
        result = geocode_to_coords("-33.5,150.2")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], -33.5, places=5)
        self.assertAlmostEqual(result[1], 150.2, places=5)

    @patch("core.services._get_geolocator")
    def test_address_calls_geocoder(self, mock_get_geo):
        mock_geo = MagicMock()
        mock_loc = MagicMock()
        mock_loc.latitude = 40.0
        mock_loc.longitude = -74.0
        mock_geo.geocode.return_value = mock_loc
        mock_get_geo.return_value = mock_geo
        result = geocode_to_coords("New York, NY")
        self.assertIsNotNone(result)
        self.assertEqual(result, (40.0, -74.0))
        mock_geo.geocode.assert_called_once()

    def test_invalid_lat_lon_out_of_bounds_returns_none(self):
        self.assertIsNone(geocode_to_coords("91, 0"))
        self.assertIsNone(geocode_to_coords("0, 181"))
        self.assertIsNone(geocode_to_coords("-91, -180"))

    def test_whitespace_only_returns_none(self):
        self.assertIsNone(geocode_to_coords("   "))
        self.assertIsNone(geocode_to_coords("\t"))


# ---------------------------------------------------------------------------
# get_route (mocking _get_http_session instead of requests.post)
# ---------------------------------------------------------------------------


class GetRouteTestCase(TestCase):
    """Tests for get_route with mocked HTTP session."""

    def _mock_session_post(self, mock_get_session, *, status_code, json_data, text=""):
        """Helper: configure the session.post mock."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_data
        mock_response.text = text
        mock_session.post.return_value = mock_response
        mock_get_session.return_value = mock_session
        return mock_session

    @patch("core.services._get_http_session")
    def test_get_route_returns_geometry_and_miles(self, mock_get_session):
        self._mock_session_post(
            mock_get_session,
            status_code=200,
            json_data={
                "features": [
                    {
                        "geometry": {"coordinates": [[-74.0, 40.0], [-73.9, 40.1]]},
                        "properties": {"summary": {"distance": 16093.44}},
                    }
                ]
            },
        )
        route_geom, total_miles = get_route((-74.0, 40.0), (-73.9, 40.1))
        self.assertIsInstance(route_geom, LineString)
        self.assertGreater(total_miles, 0)
        self.assertLess(total_miles, 20)

    @patch("core.services._get_http_session")
    def test_get_route_raises_on_api_error(self, mock_get_session):
        self._mock_session_post(
            mock_get_session,
            status_code=400,
            json_data={"error": {"message": "Route not found"}},
            text="Bad Request",
        )
        with self.assertRaises(ValueError) as ctx:
            get_route((-74.0, 40.0), (-73.0, 41.0))
        self.assertIn("Route not found", str(ctx.exception))

    @patch("core.services._get_http_session")
    def test_get_route_raises_on_empty_features(self, mock_get_session):
        self._mock_session_post(
            mock_get_session,
            status_code=200,
            json_data={"features": []},
        )
        with self.assertRaises(ValueError) as ctx:
            get_route((-74.0, 40.0), (-73.0, 41.0))
        self.assertIn("No route", str(ctx.exception))

    @patch("core.services._get_http_session")
    def test_get_route_returns_correct_miles_from_meters(self, mock_get_session):
        self._mock_session_post(
            mock_get_session,
            status_code=200,
            json_data={
                "features": [
                    {
                        "geometry": {"coordinates": [[-74.0, 40.0], [-73.9, 40.0]]},
                        "properties": {"summary": {"distance": 16093.44}},
                    }
                ]
            },
        )
        _, total_miles = get_route((-74.0, 40.0), (-73.9, 40.0))
        self.assertAlmostEqual(total_miles, 10.0, places=2)


# ---------------------------------------------------------------------------
# prefilter_stations
# ---------------------------------------------------------------------------


class PrefilterStationsTestCase(TestCase):
    """Unit tests for prefilter_stations()."""

    def test_empty_returns_empty(self):
        self.assertEqual(prefilter_stations([]), [])

    def test_single_station_passes_through(self):
        nodes = [{"mileage": 100, "price": 3.0, "name": "A"}]
        result = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "A")

    def test_two_stations_same_segment_keeps_cheapest(self):
        nodes = [
            {"mileage": 10, "price": 4.0, "name": "Expensive"},
            {"mileage": 12, "price": 2.5, "name": "Cheap"},
        ]
        result = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Cheap")

    def test_three_stations_clustered_keeps_one(self):
        """Real scenario: 3 stations within 23 miles."""
        nodes = [
            {"mileage": 800, "price": 2.837, "name": "Quiktrip"},
            {"mileage": 803, "price": 2.832, "name": "CEFCO"},
            {"mileage": 823, "price": 2.821, "name": "RACEWAY"},
        ]
        result = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "RACEWAY")

    def test_stations_in_different_segments_all_kept(self):
        nodes = [
            {"mileage": 100, "price": 3.0, "name": "A"},
            {"mileage": 300, "price": 2.5, "name": "B"},
            {"mileage": 500, "price": 2.8, "name": "C"},
        ]
        result = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(len(result), 3)

    def test_segment_size_affects_grouping(self):
        nodes = [
            {"mileage": 10, "price": 3.0, "name": "A"},
            {"mileage": 40, "price": 2.5, "name": "B"},
        ]
        result_25 = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(len(result_25), 2)
        result_50 = prefilter_stations(nodes, segment_miles=50)
        self.assertEqual(len(result_50), 1)
        self.assertEqual(result_50[0]["name"], "B")

    def test_output_preserves_node_data(self):
        nodes = [
            {
                "mileage": 100,
                "price": 3.0,
                "name": "A",
                "lat": 40.0,
                "lon": -74.0,
                "address": "123 St",
            },
        ]
        result = prefilter_stations(nodes, segment_miles=25)
        self.assertEqual(result[0]["lat"], 40.0)
        self.assertEqual(result[0]["address"], "123 St")

    def test_output_sorted_by_segment(self):
        nodes = [
            {"mileage": 500, "price": 3.0, "name": "C"},
            {"mileage": 100, "price": 2.5, "name": "A"},
            {"mileage": 300, "price": 2.8, "name": "B"},
        ]
        result = prefilter_stations(nodes, segment_miles=25)
        mileages = [n["mileage"] for n in result]
        self.assertEqual(mileages, sorted(mileages))
