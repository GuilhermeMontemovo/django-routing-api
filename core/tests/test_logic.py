from decimal import Decimal
from django.test import TestCase
from core.logic import optimize_refuel_dag


class OptimizeRefuelDAGTestCase(TestCase):
    """Unit tests for optimize_refuel_dag (DAG optimizer)."""

    def test_empty_nodes_returns_empty(self):
        path_stops, total_cost, total_gallons = optimize_refuel_dag([], 100)
        self.assertEqual(path_stops, [])
        self.assertEqual(total_cost, Decimal("0"))
        self.assertEqual(total_gallons, Decimal("0"))

    def test_single_start_finish_no_stations(self):
        nodes = [
            {"mileage": 0, "price": 0, "name": "Start"},
            {"mileage": 100, "price": 0, "name": "Finish"},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 100, range_mi=500, mpg=10)
        self.assertEqual(path_stops, [])
        self.assertEqual(total_cost, Decimal("0"))
        self.assertEqual(float(total_gallons), 10.0)  # 100 mi / 10 mpg

    def test_short_route_under_500_miles_no_stops_needed(self):
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 200, "price": 3.0, "name": "Station A"},
            {"mileage": 400, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 400, range_mi=500, mpg=10)
        # Optimal: go Start -> Finish directly (400 mi <= 500), cost 0
        self.assertEqual(len(path_stops), 0)
        self.assertEqual(total_cost, Decimal("0"))
        self.assertEqual(float(total_gallons), 40.0)

    def test_two_stations_chooses_cheaper(self):
        # Start 0 -> A at 200 ($3) -> B at 400 ($4) -> Finish 600. Range 500.
        # Optimal: 0->200->600 = 40 gal @ A $3 = $120. So A is better.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 200, "price": 3.0, "name": "A"},
            {"mileage": 400, "price": 4.0, "name": "B"},
            {"mileage": 600, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 600, range_mi=500, mpg=10)
        self.assertGreater(len(path_stops), 0)
        self.assertLessEqual(float(total_cost), 200)  # sanity
        self.assertEqual(float(total_gallons), 60.0)  # 600 mi / 10 mpg

    def test_infeasible_returns_none_cost(self):
        # Start 0, Finish 600, no station in between. Range 500 -> can't reach Finish.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 600, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 600, range_mi=500, mpg=10)
        self.assertEqual(path_stops, [])
        self.assertIsNone(total_cost)
        self.assertEqual(total_gallons, Decimal("0"))

    def test_cost_equals_sum_gallons_times_price(self):
        # 600 mi route, range 500: must stop. One station at mile 300 @ $2.5.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 300, "price": 2.5, "name": "S1"},
            {"mileage": 600, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 600, range_mi=500, mpg=10)
        self.assertEqual(len(path_stops), 1)
        self.assertEqual(path_stops[0]["name"], "S1")
        # 300 mi to S1, 300 mi to Finish: 30 gal @ S1 = 75
        self.assertAlmostEqual(path_stops[0]["gallons"], 30.0, places=5)
        self.assertAlmostEqual(path_stops[0]["cost"], 75.0, places=5)
        self.assertAlmostEqual(float(total_cost), 75.0, places=5)
        self.assertAlmostEqual(float(total_gallons), 60.0, places=5)

    def test_single_node_list_returns_early(self):
        nodes = [{"mileage": 0, "price": 0}]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 100)
        self.assertEqual(path_stops, [])
        self.assertEqual(total_cost, Decimal("0"))
        self.assertEqual(total_gallons, Decimal("0"))

    def test_three_stops_multi_refuel(self):
        # 1200 mi, range 500: needs 2+ stops. Stations at 400 ($2), 800 ($3), 1200.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 400, "price": 2.0, "name": "A"},
            {"mileage": 800, "price": 3.0, "name": "B"},
            {"mileage": 1200, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 1200, range_mi=500, mpg=10)
        self.assertGreaterEqual(len(path_stops), 2)
        self.assertAlmostEqual(float(total_gallons), 120.0, places=5)  # 1200 / 10
        self.assertGreater(float(total_cost), 0)
        # Cost = sum(gallons_i * price_i) at each stop
        computed = sum(s["cost"] for s in path_stops)
        self.assertAlmostEqual(computed, float(total_cost), places=2)

    def test_cheapest_in_segment_preferred(self):
        # 0-500: station A $2; 500-1000: station B $5. Optimal: stop at A.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 250, "price": 2.0, "name": "A"},
            {"mileage": 750, "price": 5.0, "name": "B"},
            {"mileage": 1000, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(nodes, 1000, range_mi=500, mpg=10)
        self.assertEqual(len(path_stops), 2)  # one stop per 500-mile window
        names = [s["name"] for s in path_stops]
        self.assertIn("A", names)
        self.assertIn("B", names)
        # First stop should be A (cheapest in the first segment)
        self.assertEqual(path_stops[0]["name"], "A")

    def test_path_stops_have_expected_keys(self):
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 300, "price": 2.0, "name": "S1", "address": "123 Main St", "lat": 40.0, "lon": -74.0},
            {"mileage": 600, "price": 0},
        ]
        path_stops, _, _ = optimize_refuel_dag(nodes, 600, range_mi=500, mpg=10)
        self.assertEqual(len(path_stops), 1)
        stop = path_stops[0]
        for key in ("mileage", "price", "name", "gallons", "cost"):
            self.assertIn(key, stop)
        self.assertAlmostEqual(stop["gallons"], 30.0, places=5)
        self.assertAlmostEqual(stop["cost"], 60.0, places=5)

    def test_custom_mpg_and_range(self):
        # 200 mi, range 100, mpg 20: must stop once. Station at mile 100 @ $4.
        # Path: 0 -> 100 (S1) -> 200. At S1 buy for 100->200 = 5 gal @ $4 = $20.
        nodes = [
            {"mileage": 0, "price": 0},
            {"mileage": 100, "price": 4.0, "name": "S1"},
            {"mileage": 200, "price": 0},
        ]
        path_stops, total_cost, total_gallons = optimize_refuel_dag(
            nodes, 200, range_mi=100, mpg=20
        )
        self.assertEqual(len(path_stops), 1)
        self.assertAlmostEqual(path_stops[0]["gallons"], 5.0, places=5)  # 100 mi / 20 mpg (S1->Finish)
        self.assertAlmostEqual(float(total_cost), 20.0, places=5)  # 5 gal * $4
        self.assertAlmostEqual(float(total_gallons), 10.0, places=5)  # 200 mi / 20 mpg total
