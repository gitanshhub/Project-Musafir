"""
Project Musafir — Derived State Freshness Engine (Milestone 3, Component 4)
Formalizes explicit freshness lifecycle (VALID, STALE, NOT_AVAILABLE) and state version
provenance for each canonical derived planning resource.
Pure Python, deterministic, and stateless.
"""

from enum import Enum
from typing import Any, Dict, Iterable, Optional, Set, Union
from pydantic import BaseModel, Field

from app.agent.dependencies import DerivedResource, DEPENDENCY_GRAPH, get_invalidated_resources


class DerivedStateStatus(str, Enum):
    """
    Canonical enumeration of derived resource freshness states.
    - VALID: Result corresponds to the active TripState inputs and is trustworthy.
    - STALE: Result previously existed, but a source dependency changed, making it obsolete.
    - NOT_AVAILABLE: No result currently exists (initial absence or never computed).
    """
    VALID = "valid"
    STALE = "stale"
    NOT_AVAILABLE = "not_available"


class DerivedResourceFreshness(BaseModel):
    """
    Metadata container tracking freshness status and state-version provenance
    for a single canonical derived planning resource.
    """
    resource: DerivedResource
    status: DerivedStateStatus = DerivedStateStatus.NOT_AVAILABLE
    state_version: Optional[int] = Field(
        default=None,
        description="The TripState.state_version against which this derived result was generated"
    )

    def is_valid(self) -> bool:
        """Returns True if the resource is currently VALID."""
        return self.status == DerivedStateStatus.VALID

    def is_stale(self) -> bool:
        """Returns True if the resource is currently STALE."""
        return self.status == DerivedStateStatus.STALE

    def is_available(self) -> bool:
        """Returns True if a result exists (either VALID or STALE, not NOT_AVAILABLE)."""
        return self.status != DerivedStateStatus.NOT_AVAILABLE


def init_derived_freshness() -> Dict[str, DerivedResourceFreshness]:
    """
    Initializes default freshness entries for all canonical DerivedResources.
    Every resource begins in NOT_AVAILABLE status with no provenance version.
    """
    return {
        r.value: DerivedResourceFreshness(
            resource=r,
            status=DerivedStateStatus.NOT_AVAILABLE,
            state_version=None,
        )
        for r in DerivedResource
    }


class FreshnessRegistry:
    """
    Deterministic helper for inspecting, transitioning, and validating
    freshness states across derived resources.
    """

    @staticmethod
    def get_status(
        freshness_map: Dict[str, DerivedResourceFreshness],
        resource: Union[DerivedResource, str],
    ) -> DerivedStateStatus:
        """Retrieves the current DerivedStateStatus for a given resource."""
        key = resource.value if isinstance(resource, DerivedResource) else str(resource)
        entry = freshness_map.get(key)
        return entry.status if entry else DerivedStateStatus.NOT_AVAILABLE

    @staticmethod
    def get_version(
        freshness_map: Dict[str, DerivedResourceFreshness],
        resource: Union[DerivedResource, str],
    ) -> Optional[int]:
        """Retrieves the state_version provenance for a given resource."""
        key = resource.value if isinstance(resource, DerivedResource) else str(resource)
        entry = freshness_map.get(key)
        return entry.state_version if entry else None

    @staticmethod
    def mark_valid(
        freshness_map: Dict[str, DerivedResourceFreshness],
        resource: Union[DerivedResource, str],
        current_state_version: int,
        result_state_version: Optional[int] = None,
    ) -> bool:
        """
        Marks a derived resource as VALID, recording provenance state_version.
        Enforces stale-response protection:
        If result_state_version is provided and result_state_version < current_state_version,
        the result is rejected as obsolete and the status is NOT updated.
        Returns True if marked VALID, False if rejected due to stale version.
        """
        key = resource.value if isinstance(resource, DerivedResource) else str(resource)
        r_enum = resource if isinstance(resource, DerivedResource) else DerivedResource(key)

        # Provenance protection against delayed stale results
        if result_state_version is not None and result_state_version < current_state_version:
            return False

        version = result_state_version if result_state_version is not None else current_state_version
        freshness_map[key] = DerivedResourceFreshness(
            resource=r_enum,
            status=DerivedStateStatus.VALID,
            state_version=version,
        )
        return True

    @staticmethod
    def mark_stale(
        freshness_map: Dict[str, DerivedResourceFreshness],
        resource: Union[DerivedResource, str],
    ) -> bool:
        """
        Transitions an existing resource from VALID to STALE.
        If the resource is currently NOT_AVAILABLE, it strictly remains NOT_AVAILABLE.
        Returns True if the resource became or was STALE, False if it was NOT_AVAILABLE.
        """
        key = resource.value if isinstance(resource, DerivedResource) else str(resource)
        r_enum = resource if isinstance(resource, DerivedResource) else DerivedResource(key)
        entry = freshness_map.get(key)

        if not entry or entry.status == DerivedStateStatus.NOT_AVAILABLE:
            return False

        freshness_map[key] = DerivedResourceFreshness(
            resource=r_enum,
            status=DerivedStateStatus.STALE,
            state_version=entry.state_version,
        )
        return True

    @staticmethod
    def mark_not_available(
        freshness_map: Dict[str, DerivedResourceFreshness],
        resource: Union[DerivedResource, str],
    ) -> None:
        """Explicitly resets a derived resource to NOT_AVAILABLE status."""
        key = resource.value if isinstance(resource, DerivedResource) else str(resource)
        r_enum = resource if isinstance(resource, DerivedResource) else DerivedResource(key)
        freshness_map[key] = DerivedResourceFreshness(
            resource=r_enum,
            status=DerivedStateStatus.NOT_AVAILABLE,
            state_version=None,
        )

    @staticmethod
    def apply_invalidation(
        freshness_map: Dict[str, DerivedResourceFreshness],
        invalidated_resources: Iterable[DerivedResource],
    ) -> Set[DerivedResource]:
        """
        Applies dependency invalidation to the freshness map:
        - If an invalidated resource is currently VALID, transitions it to STALE.
        - If an invalidated resource is currently STALE, retains STALE.
        - If an invalidated resource is currently NOT_AVAILABLE, retains NOT_AVAILABLE.
        Returns the set of resources that were transitioned from VALID to STALE.
        """
        newly_stale: Set[DerivedResource] = set()
        for res in invalidated_resources:
            key = res.value
            entry = freshness_map.get(key)
            if entry and entry.status == DerivedStateStatus.VALID:
                freshness_map[key] = DerivedResourceFreshness(
                    resource=res,
                    status=DerivedStateStatus.STALE,
                    state_version=entry.state_version,
                )
                newly_stale.add(res)
        return newly_stale
