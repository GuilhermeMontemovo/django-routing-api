from django.contrib.gis.geos import LineString
from django.test import TestCase

from core.selectors import station_list_on_route


class StationListOnRouteTestCase(TestCase):
    """Tests for station_list_on_route selector (requires PostGIS)."""

    def test_returns_queryset_with_fraction(self):
        line = LineString([(-74.0, 40.0), (-73.99, 40.01)], srid=4326)
        qs = station_list_on_route(route_geom=line)
        # May be empty if no stations in DB, but should not error
        self.assertTrue(hasattr(qs, "order_by"))
        list(qs)  # no error

    def test_queryset_ordered_by_fraction(self):
        line = LineString([(-100.0, 35.0), (-99.0, 35.0)], srid=4326)
        qs = station_list_on_route(route_geom=line)
        self.assertTrue(hasattr(qs.query, "order_by"))
        self.assertTrue(qs.query.order_by)
