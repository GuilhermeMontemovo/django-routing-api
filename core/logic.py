"""
Refueling DAG — shortest path on a DAG for minimum fuel cost.

Nodes: Start (mile 0), stations (mileage = fraction x total_miles), Finish.
Edges: A -> B iff mileage(B) - mileage(A) <= range_mi.
Weight: (dist / mpg) x price(A).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TypedDict

from core.constants import VEHICLE_MPG, VEHICLE_RANGE_MI


class RouteNode(TypedDict, total=False):
    """Type for DAG nodes (Start, stations, and Finish)."""

    mileage: float
    price: float
    lat: float
    lon: float
    name: str
    address: str
    station_id: int | None
    # Populated after optimization (only for stops in the path)
    gallons: float
    cost: float


def optimize_refuel_dag(
    nodes: list[RouteNode],
    total_miles: float,
    range_mi: int = VEHICLE_RANGE_MI,
    mpg: int = VEHICLE_MPG,
) -> tuple[list[RouteNode], Decimal | None, Decimal]:
    """
    Find the minimum-cost path from Start to Finish using DP on the DAG.

    Args:
        nodes: list sorted by mileage; ``nodes[0]`` = Start, ``nodes[-1]`` = Finish.
        total_miles: total route distance in miles.
        range_mi: vehicle range in miles.
        mpg: fuel economy in miles per gallon.

    Returns:
        ``(path_stops, total_cost, total_gallons)``
        — ``path_stops`` excludes Start/Finish.
        — ``total_cost`` is ``None`` if no viable path exists.
    """
    if not nodes or len(nodes) < 2:
        return [], Decimal("0"), Decimal("0")

    n: int = len(nodes)
    INF: float = math.inf

    mileages: list[float] = [float(nodes[i].get("mileage", 0)) for i in range(n)]
    prices: list[float] = [float(nodes[i].get("price", 0)) for i in range(n)]
    prices[0] = 0.0  # Start never charges

    min_cost: list[float] = [INF] * n
    parent: list[int | None] = [None] * n
    min_cost[0] = 0.0

    # Topological order = index order (nodes already sorted by mileage)
    for i in range(n):
        if min_cost[i] == INF:
            continue
        for j in range(i + 1, n):
            dist_ij: float = mileages[j] - mileages[i]
            if dist_ij > range_mi:
                break
            gallons_ij: float = dist_ij / mpg
            cost_ij: float = gallons_ij * prices[i]
            new_cost: float = min_cost[i] + cost_ij
            if new_cost < min_cost[j]:
                min_cost[j] = new_cost
                parent[j] = i

    # Reconstruct path Finish -> Start
    path_indices: list[int] = []
    cur: int | None = n - 1
    while cur is not None:
        path_indices.append(cur)
        cur = parent[cur]
    path_indices.reverse()

    if path_indices[0] != 0:
        return [], None, Decimal("0")

    # Build stops with gallons/cost
    path_stops: list[RouteNode] = []
    total_cost = Decimal("0")
    total_gallons = Decimal("0")

    for idx in range(len(path_indices) - 1):
        i: int = path_indices[idx]
        j: int = path_indices[idx + 1]
        dist_ij = mileages[j] - mileages[i]
        gallons_ij_d = Decimal(str(dist_ij)) / Decimal(str(mpg))
        cost_ij_d = gallons_ij_d * Decimal(str(prices[i]))
        total_gallons += gallons_ij_d
        total_cost += cost_ij_d

        if i > 0:  # exclude Start
            stop = RouteNode(**nodes[i])
            stop["gallons"] = float(gallons_ij_d)
            stop["cost"] = float(cost_ij_d)
            path_stops.append(stop)

    return path_stops, total_cost, total_gallons
