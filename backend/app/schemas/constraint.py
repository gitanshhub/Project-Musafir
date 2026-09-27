"""
Project Musafir — Canonical Constraint & Feasibility Schemas (Milestone 3, Batch 3)
Defines the core domain models for:
- Constraint representation (Priority, Scope, Type, Status, Source)
- Required vs. Preferred vs. Optional stop classification
- Feasibility status and structured ConstraintViolations
- FeasibilityResult with scheduled/unscheduled items and day metrics
"""

from enum import Enum
import uuid
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# =============================================================================
# 1. CONSTRAINT MODEL ENUMERATIONS
# =============================================================================

class ConstraintPriority(str, Enum):
    """
    Explicit hierarchy of constraint importance.
    CRITICAL INVARIANT: A soft/PREFERRED preference must never silently override a hard/REQUIRED constraint.
    The system must never silently relax a REQUIRED constraint.
    """
    REQUIRED = "REQUIRED"      # Hard constraint: explicit dates, budget ceiling, travel mode, must-visit stops.
    PREFERRED = "PREFERRED"    # Soft preference: cuisine preference, pacing, preferred area.
    OPTIONAL = "OPTIONAL"      # Nice-to-have: low-priority discovery suggestions.


class ConstraintScope(str, Enum):
    """Domain scope governed by a constraint."""
    TRIP = "TRIP"              # Trip-wide duration, overall dates, general policy
    HOTEL = "HOTEL"            # Accommodation selection, nightly rate ceiling, total hotel budget
    PLACE = "PLACE"            # Attraction stops, required vs optional visits
    FOOD = "FOOD"              # Dietary restrictions, cuisine preferences, meal timings
    ROUTE = "ROUTE"            # Travel mode, maximum transit leg duration
    TIME = "TIME"              # Daily sightseeing windows, start/end hours
    ITINERARY = "ITINERARY"    # Day capacity, pacing, stop sequencing


class ConstraintType(str, Enum):
    """Canonical constraint categories."""
    TRIP_DATES = "TRIP_DATES"
    TRIP_DURATION = "TRIP_DURATION"
    HOTEL_BUDGET_CEILING = "HOTEL_BUDGET_CEILING"
    HOTEL_TOTAL_BUDGET = "HOTEL_TOTAL_BUDGET"
    TRIP_TOTAL_BUDGET = "TRIP_TOTAL_BUDGET"
    TRAVEL_MODE = "TRAVEL_MODE"
    MUST_VISIT_STOP = "MUST_VISIT_STOP"
    PREFERRED_STOP = "PREFERRED_STOP"
    OPTIONAL_STOP = "OPTIONAL_STOP"
    MEAL_WINDOW = "MEAL_WINDOW"
    DAILY_TIME_LIMIT = "DAILY_TIME_LIMIT"
    DIETARY_RESTRICTION = "DIETARY_RESTRICTION"
    CUISINE_PREFERENCE = "CUISINE_PREFERENCE"
    MAX_WALKING_DISTANCE = "MAX_WALKING_DISTANCE"


class ConstraintStatus(str, Enum):
    """Lifecycle status of a constraint within the planning session."""
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    RELAXED = "RELAXED"


class ConstraintSource(str, Enum):
    """
    Provenance of a constraint to prevent treating system defaults as user-requested.
    """
    USER_EXPLICIT = "USER_EXPLICIT"      # Directly specified by traveler ("must visit Fort Kochi", "only walking")
    USER_PREFERENCE = "USER_PREFERENCE"  # User preference ("I prefer vegetarian", "budget around 5000")
    SYSTEM_DERIVED = "SYSTEM_DERIVED"    # Default operational assumptions (09:00-20:00 schedule, 1.5h lunch)


# =============================================================================
# 2. CANONICAL CONSTRAINT MODEL
# =============================================================================

class Constraint(BaseModel):
    """
    Canonical constraint representation governing trip planning feasibility.
    """
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8],
        description="Unique identifier for this constraint instance"
    )
    constraint_type: ConstraintType = Field(..., description="Category of constraint")
    scope: ConstraintScope = Field(..., description="Domain scope of the constraint")
    priority: ConstraintPriority = Field(..., description="REQUIRED (hard) vs PREFERRED (soft) vs OPTIONAL")
    source: ConstraintSource = Field(..., description="Source of constraint (USER_EXPLICIT, USER_PREFERENCE, SYSTEM_DERIVED)")
    value: Any = Field(None, description="Constraint threshold, value, or parameter")
    target_entity: Optional[str] = Field(None, description="Entity name or token if bound to a specific item")
    status: ConstraintStatus = Field(default=ConstraintStatus.ACTIVE, description="Current evaluation status")
    explanation: Optional[str] = Field(None, description="Human-readable description or rationale")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context or provenance data")

    @property
    def is_hard(self) -> bool:
        """Helper to determine if constraint is strictly REQUIRED."""
        return self.priority == ConstraintPriority.REQUIRED


# =============================================================================
# 3. FEASIBILITY & VIOLATION SCHEMAS
# =============================================================================

class FeasibilityStatus(str, Enum):
    """Top-level evaluation outcome of schedule and constraint feasibility."""
    FEASIBLE = "FEASIBLE"                      # All constraints satisfied, timetable fits cleanly
    INFEASIBLE = "INFEASIBLE"                  # One or more REQUIRED constraints cannot be satisfied
    PARTIALLY_FEASIBLE = "PARTIALLY_FEASIBLE"  # Hard constraints satisfied, but some PREFERRED/OPTIONAL stops exceed capacity
    UNKNOWN = "UNKNOWN"                        # Insufficient inputs to evaluate


class ConstraintViolationType(str, Enum):
    """Vocabulary of deterministic conflicts surfaced by the Feasibility Engine."""
    DAY_CAPACITY_EXCEEDED = "DAY_CAPACITY_EXCEEDED"      # Available day hours < required visit + travel time
    MEAL_WINDOW_CONFLICT = "MEAL_WINDOW_CONFLICT"        # Lunch/dinner stop unreachable during meal window
    OPENING_HOURS_CONFLICT = "OPENING_HOURS_CONFLICT"    # Visit falls outside opening/closing hours
    TRAVEL_TIME_CONFLICT = "TRAVEL_TIME_CONFLICT"        # Transit between stops exceeds acceptable time/distance
    TRIP_DURATION_CONFLICT = "TRIP_DURATION_CONFLICT"    # Required stops cannot fit into total trip days
    BUDGET_CONFLICT = "BUDGET_CONFLICT"                  # Selected hotel exceeds budget ceiling
    MODE_CONFLICT = "MODE_CONFLICT"                      # Travel mode (e.g. walking) impractical for required distance
    REQUIRED_STOP_CONFLICT = "REQUIRED_STOP_CONFLICT"    # A MUST_VISIT stop could not be scheduled


class ConstraintViolation(BaseModel):
    """
    Structured, explainable record of a constraint violation.
    Contains both raw numbers for calculation and formatted strings for traveler communication.
    """
    violation_type: ConstraintViolationType = Field(..., description="Specific violation classification")
    affected_constraint: Optional[Constraint] = Field(None, description="The constraint that was violated")
    affected_items: List[str] = Field(default_factory=list, description="Names of stops, hotels, or items involved")
    actual_value: Any = Field(None, description="Observed / calculated value causing violation (e.g. '11.5h')")
    required_value: Any = Field(None, description="Allowed threshold or window (e.g. '9.0h')")
    excess_or_deficit: Any = Field(None, description="Difference (e.g. '+2.5h' over capacity)")
    severity: str = Field(default="HIGH", description="'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'")
    explanation: str = Field(..., description="Deterministic, human-readable summary of the conflict")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Detailed metrics, coordinates, or timing data")


class DayScheduleMetrics(BaseModel):
    """Timing breakdown for a single day of the trip schedule."""
    day_number: int = Field(..., description="1-indexed day number")
    date_str: Optional[str] = Field(None, description="ISO or readable date string if set")
    available_hours: float = Field(..., description="Total waking window available (e.g. 11.0h)")
    required_sightseeing_hours: float = Field(0.0, description="Sum of attraction visit durations")
    required_travel_hours: float = Field(0.0, description="Sum of transit times between stops")
    required_meal_hours: float = Field(0.0, description="Sum of dining durations")
    total_required_hours: float = Field(..., description="Sightseeing + travel + meal hours")
    excess_hours: float = Field(0.0, description="Max(0, total_required_hours - available_hours)")
    stops: List[str] = Field(default_factory=list, description="Names of stops scheduled on this day")


class FeasibilityResult(BaseModel):
    """
    Canonical output of the Feasibility Engine.
    Communicates the exact status, scheduled vs unscheduled items, and all violations.
    """
    status: FeasibilityStatus = Field(..., description="Overall feasibility classification")
    scheduled_items: List[str] = Field(
        default_factory=list,
        description="Names of places and restaurants successfully allocated into feasible schedule"
    )
    unscheduled_items: List[str] = Field(
        default_factory=list,
        description="Names of places or restaurants that could not fit or were conflicted"
    )
    violations: List[ConstraintViolation] = Field(
        default_factory=list,
        description="List of detected constraint violations"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Non-fatal observations or soft warnings"
    )
    day_metrics: List[DayScheduleMetrics] = Field(
        default_factory=list,
        description="Day-by-day timing analysis"
    )
    total_available_hours: float = Field(0.0, description="Total hours available across all trip days")
    total_required_hours: float = Field(0.0, description="Total hours required for all requested activities")
    excess_hours: float = Field(0.0, description="Total time over capacity across the trip")

    @property
    def is_feasible(self) -> bool:
        return self.status == FeasibilityStatus.FEASIBLE

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0
