"""
API tests for RoutePlanApi — mocks the service layer (core.views.route_plan).
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.gis.geos import LineString
from rest_framework import status
from rest_framework.test import APITestCase


class RoutePlanApiTestCase(APITestCase):
    """API tests for RoutePlanApi."""

    def test_missing_start_returns_400(self):
        response = self.client.get("/api/route/", {"end": "40.7,-74.0"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_end_returns_400(self):
        response = self.client.get("/api/route/", {"start": "40.7,-74.0"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("core.views.route_plan")
    def test_valid_request_returns_200_and_schema(self, mock_route_plan):
        mock_route_plan.return_value = {
            "route_geojson": {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-74.0, 40.0], [-73.0, 41.0]],
                },
                "properties": {},
            },
            "stops": [],
            "total_fuel_cost": 0.0,
            "total_gallons": 10.0,
            "total_miles": 100.0,
            "mpg_used": 10,
        }

        response = self.client.get(
            "/api/route/",
            {"start": "40,-74", "end": "41,-73"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()
        self.assertIn("route_geojson", data)
        self.assertIn("stops", data)
        self.assertIn("total_fuel_cost", data)
        self.assertIn("total_gallons", data)
        self.assertIn("total_miles", data)
        self.assertIn("mpg_used", data)
        self.assertEqual(data["mpg_used"], 10)
        self.assertIsInstance(data["stops"], list)
        self.assertIsInstance(data["route_geojson"], dict)
        self.assertEqual(data["route_geojson"].get("type"), "Feature")

    @patch("core.views.route_plan")
    def test_unresolved_start_returns_400(self, mock_route_plan):
        mock_route_plan.side_effect = ValueError("Could not resolve start location.")
        response = self.client.get(
            "/api/route/",
            {"start": "nowhere", "end": "41,-73"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("start", response.json().get("error", "").lower())

    @patch("core.views.route_plan")
    def test_unresolved_end_returns_400(self, mock_route_plan):
        mock_route_plan.side_effect = ValueError("Could not resolve end location.")
        response = self.client.get(
            "/api/route/",
            {"start": "40,-74", "end": "nowhere"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end", response.json().get("error", "").lower())

    @patch("core.views.route_plan")
    def test_route_error_returns_400(self, mock_route_plan):
        mock_route_plan.side_effect = ValueError("Route not found")
        response = self.client.get(
            "/api/route/",
            {"start": "40,-74", "end": "41,-73"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.json())

    @patch("core.views.route_plan")
    def test_no_feasible_path_returns_400(self, mock_route_plan):
        mock_route_plan.side_effect = ValueError(
            "No feasible refuel path (e.g. segment > 500 miles without a station)."
        )
        response = self.client.get(
            "/api/route/",
            {"start": "40,-74", "end": "41,-73"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.json())

    @patch("core.views.route_plan")
    def test_post_with_json_body_accepts_start_end(self, mock_route_plan):
        mock_route_plan.return_value = {
            "route_geojson": {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-74.0, 40.0], [-73.0, 41.0]],
                },
                "properties": {},
            },
            "stops": [],
            "total_fuel_cost": 0.0,
            "total_gallons": 10.0,
            "total_miles": 100.0,
            "mpg_used": 10,
        }
        response = self.client.post(
            "/api/route/",
            {"start": "40,-74", "end": "41,-73"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("total_fuel_cost", response.json())

    @patch("core.views.route_plan")
    def test_response_stops_have_expected_fields_when_non_empty(self, mock_route_plan):
        mock_route_plan.return_value = {
            "route_geojson": {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-74.0, 40.0], [-73.0, 41.0]],
                },
                "properties": {},
            },
            "stops": [
                {
                    "mileage": 300.0,
                    "lat": 40.5,
                    "lon": -73.5,
                    "name": "Stop A",
                    "address": "Addr",
                    "price": 3.0,
                    "gallons": 30.0,
                    "cost": 90.0,
                }
            ],
            "total_fuel_cost": 90.0,
            "total_gallons": 30.0,
            "total_miles": 600.0,
            "mpg_used": 10,
        }
        response = self.client.get(
            "/api/route/",
            {"start": "40,-74", "end": "41,-73"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data["stops"]), 1)
        stop = data["stops"][0]
        for key in ("mileage", "lat", "lon", "name", "address", "price", "gallons", "cost"):
            self.assertIn(key, stop)
        self.assertEqual(stop["name"], "Stop A")
        self.assertEqual(stop["cost"], 90.0)
