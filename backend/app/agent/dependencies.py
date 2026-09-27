"""
Project Musafir — Centralized Dependency & Invalidation Engine (Milestone 3, Component 3)
Formalizes deterministic dependency relationships between TripState source fields
and derived planning resources.
Pure Python, stateless, with zero external network or LLM dependencies.
"""

from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Set, Union, Optional


class DerivedResource(str, Enum):
    """
    Canonical enumeration of derived planning resources in Project Musafir.
    Represents computed, synthesized, or discovered artifacts that depend on TripState inputs.
    """
    HOTEL_DISCOVERY = "hotel_discovery"
    PLACE_DISCOVERY = "place_discovery"
    FOOD_DISCOVERY = "food_discovery"
    CURRENT_ROUTE = "current_route"
    CURRENT_ITINERARY = "current_itinerary"


# Explicit, canonical dependency graph mapping source state field names to their dependent DerivedResources.
# All relationships are grounded in actual repository domain architecture.
DEPENDENCY_GRAPH: Dict[str, FrozenSet[DerivedResource]] = {
    # 1. Primary Trip Anchor
    # Destination changes invalidate all discovery and downstream computed artifacts.
    "destination": frozenset({
        DerivedResource.HOTEL_DISCOVERY,
        DerivedResource.PLACE_DISCOVERY,
        DerivedResource.FOOD_DISCOVERY,
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 2. Selected Accommodation Anchor
    # Places and food discovery are spatially anchored to the selected hotel (via lat/lng).
    # Route begins/ends at the hotel.
    # Note: changing selected hotel does NOT invalidate HOTEL_DISCOVERY (available hotel list remains valid).
    "hotel_selection": frozenset({
        DerivedResource.PLACE_DISCOVERY,
        DerivedResource.FOOD_DISCOVERY,
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "selected_hotel": frozenset({
        DerivedResource.PLACE_DISCOVERY,
        DerivedResource.FOOD_DISCOVERY,
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 3. Selected Attractions / Stops
    # Adding or removing stops directly alters route optimization and the day itinerary schedule.
    # Note: does NOT invalidate PLACE_DISCOVERY (available place search results remain valid).
    "selected_places": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "remove_places": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "rejected_places": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 4. Selected Food Stops (Restaurants & Cafes)
    # Food stops are routed stops and meal schedule blocks.
    # Note: does NOT invalidate FOOD_DISCOVERY (dining search results remain valid).
    "selected_restaurants": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "remove_restaurants": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "rejected_restaurants": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "selected_cafes": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "remove_cafes": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),
    "rejected_cafes": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 5. Travel Mode
    # Modifies routing graph leg transit times and polyline geometry.
    # Note: does NOT invalidate discovery results (hotels, places, food remain unchanged).
    "travel_mode": frozenset({
        DerivedResource.CURRENT_ROUTE,
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 6. Trip Timing & Duration
    # Modifies the day-by-day timetable mapping, day breaks, and date calendar headers.
    # Note: does NOT invalidate route geometry/stops unless stops themselves change.
    "number_of_days": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "number_of_nights": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "trip_start_date": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "trip_end_date": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "start_time": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "day_start_time": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "day_end_time": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),

    # 7. Interests & Theme Preferences
    # Filters/affects attraction recommendations.
    "interests": frozenset({
        DerivedResource.PLACE_DISCOVERY,
    }),

    # 8. Dining Preferences
    # Affects restaurant and cafe discovery queries and filters.
    "cuisine_preferences": frozenset({
        DerivedResource.FOOD_DISCOVERY,
    }),
    "dietary_preferences": frozenset({
        DerivedResource.FOOD_DISCOVERY,
    }),
    "food_price_preference": frozenset({
        DerivedResource.FOOD_DISCOVERY,
    }),
    "meal_preferences": frozenset({
        DerivedResource.FOOD_DISCOVERY,
    }),

    # 9. Constraints & Priorities (Milestone 3, Batch 3)
    # Changing priority/constraints invalidates itinerary scheduling without dropping discovery or route geometry.
    "required_stops": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),
    "constraints": frozenset({
        DerivedResource.CURRENT_ITINERARY,
    }),


    # 9. Budget Ceilings
    # Modifies hotel search pricing filters.
    "hotel_budget": frozenset({
        DerivedResource.HOTEL_DISCOVERY,
    }),
    "hotel_total_budget": frozenset({
        DerivedResource.HOTEL_DISCOVERY,
    }),
}


def get_invalidated_resources(changed_fields: Iterable[str]) -> Set[DerivedResource]:
    """
    Pure deterministic invalidation calculator.
    Consults the dependency graph to determine all derived resources
    invalidated by the given set of changed or invalidated source fields.
    Deduplicates output and produces zero side effects.
    """
    invalidated: Set[DerivedResource] = set()
    for field in changed_fields:
        if not field:
            continue
        field_name = str(field).strip()
        if field_name in DEPENDENCY_GRAPH:
            invalidated.update(DEPENDENCY_GRAPH[field_name])
    return invalidated


def get_field_dependencies(source_field: str) -> Set[DerivedResource]:
    """
    Returns the set of derived resources directly dependent on a single source field.
    Returns an empty set for unknown or non-dependent fields.
    """
    if not source_field:
        return set()
    return set(DEPENDENCY_GRAPH.get(str(source_field).strip(), frozenset()))


def is_resource_invalidated(resource: DerivedResource, changed_fields: Iterable[str]) -> bool:
    """
    Convenience helper checking whether a specific derived resource
    is invalidated by any of the provided changed fields.
    """
    return resource in get_invalidated_resources(changed_fields)
