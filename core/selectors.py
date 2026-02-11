"""
Selectors — database *read* functions.

Following the HackSoft Django Styleguide: selectors never mutate data,
they only query and return QuerySets or derived values.
"""

from django.contrib.gis.db.models.functions import LineLocatePoint
from django.contrib.gis.geos import LineString
from django.db.models import QuerySet

from core.constants import DEGREES_PER_MILE, STATION_BUFFER_MI
from core.models import FuelStation


def station_list_on_route(*, route_geom: LineString) -> QuerySet[FuelStation]:
    """
    Return stations within a ~STATION_BUFFER_MI mile buffer of the route,
    annotated with ``fraction`` (0.0 -> 1.0) for their linear position on the route.
    """
    buffer_degrees: float = STATION_BUFFER_MI * DEGREES_PER_MILE
    return (
        FuelStation.objects
        .filter(location__dwithin=(route_geom, buffer_degrees))
        .annotate(fraction=LineLocatePoint(route_geom, "location"))
        .only("opis_id", "name", "address", "retail_price", "location")
        .order_by("fraction")
    )
