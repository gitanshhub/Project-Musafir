"""
Project Musafir — Agent Mutation Module (Milestone 3, Component 1 & 2)
Exposes canonical mutation types, budget scopes, command containers,
and the mutation application adapter connecting MutationCommand / MutationBatch
directly into the canonical apply_trip_state_update engine.
"""

from typing import Any, Dict, List, Optional, Union
from app.schemas.mutation import (
    MutationType,
    BudgetScope,
    ResolutionConfidence,
    MutationCommand,
    MutationBatch,
    SUPPORTED_TRAVEL_MODES,
    VALID_PREFERENCE_CATEGORIES,
)
from app.schemas.reference import EntityReference, VisibleItemReference
from app.agent.state import TripState
from app.agent.state_update import apply_trip_state_update, TripChangeSet

__all__ = [
    "MutationType",
    "BudgetScope",
    "ResolutionConfidence",
    "MutationCommand",
    "MutationBatch",
    "EntityReference",
    "VisibleItemReference",
    "SUPPORTED_TRAVEL_MODES",
    "VALID_PREFERENCE_CATEGORIES",
    "mutation_to_state_updates",
    "apply_mutation_command",
    "apply_mutation_batch",
]


def mutation_to_state_updates(command: MutationCommand) -> Dict[str, Any]:
    """
    Translates a validated MutationCommand into the canonical dictionary
    of updates consumed by apply_trip_state_update.
    """
    m_type = command.mutation_type

    if m_type == MutationType.SET_DESTINATION:
        return {"destination": str(command.value).strip()}

    elif m_type == MutationType.SET_START_DATE:
        return {"trip_start_date": command.value}

    elif m_type == MutationType.SET_END_DATE:
        return {"trip_end_date": command.value}

    elif m_type == MutationType.SET_DURATION:
        return {"number_of_days": int(command.value)}

    elif m_type == MutationType.SET_BUDGET:
        if command.scope == BudgetScope.NIGHTLY_HOTEL:
            return {"hotel_budget": float(command.value)}
        elif command.scope == BudgetScope.TOTAL_HOTEL:
            return {"hotel_total_budget": float(command.value)}
        elif command.scope == BudgetScope.TOTAL_TRIP:
            return {"trip_budget": float(command.value)}
        else:
            raise ValueError(f"Unknown or missing budget scope: {command.scope}")

    elif m_type == MutationType.SET_TRAVEL_MODE:
        return {"travel_mode": str(command.value).strip().lower()}

    elif m_type in (MutationType.SELECT_HOTEL, MutationType.CHANGE_HOTEL):
        hotel_data = None
        if command.reference:
            hotel_data = command.reference.data or {"name": command.reference.name, "id": command.reference.id}
        elif command.value is not None:
            hotel_data = command.value
        elif command.target_name or command.target_id:
            hotel_data = {"name": command.target_name or command.target_id, "id": command.target_id}
        return {"hotel_selection": hotel_data}

    elif m_type == MutationType.SELECT_PLACE:
        place_data = None
        if command.reference:
            place_data = command.reference.data or {"name": command.reference.name, "data_id": command.reference.id}
        elif command.value is not None:
            place_data = command.value
        elif command.target_name or command.target_id:
            place_data = {"name": command.target_name or command.target_id, "data_id": command.target_id or command.target_name}
        return {"selected_places": [place_data]}

    elif m_type == MutationType.REMOVE_PLACE:
        ident = command.target_id or command.target_name or (command.reference.id if command.reference else None) or (command.reference.name if command.reference else None) or command.value
        return {"remove_places": [str(ident)]}

    elif m_type == MutationType.SELECT_RESTAURANT:
        rest_data = None
        if command.reference:
            rest_data = command.reference.data or {"name": command.reference.name, "data_id": command.reference.id}
        elif command.value is not None:
            rest_data = command.value
        elif command.target_name or command.target_id:
            rest_data = {"name": command.target_name or command.target_id, "data_id": command.target_id or command.target_name}
        return {"selected_restaurants": [rest_data]}

    elif m_type == MutationType.REMOVE_RESTAURANT:
        ident = command.target_id or command.target_name or (command.reference.id if command.reference else None) or (command.reference.name if command.reference else None) or command.value
        return {"remove_restaurants": [str(ident)]}

    elif m_type == MutationType.SELECT_CAFE:
        cafe_data = None
        if command.reference:
            cafe_data = command.reference.data or {"name": command.reference.name, "data_id": command.reference.id}
        elif command.value is not None:
            cafe_data = command.value
        elif command.target_name or command.target_id:
            cafe_data = {"name": command.target_name or command.target_id, "data_id": command.target_id or command.target_name}
        return {"selected_cafes": [cafe_data]}

    elif m_type == MutationType.REMOVE_CAFE:
        ident = command.target_id or command.target_name or (command.reference.id if command.reference else None) or (command.reference.name if command.reference else None) or command.value
        return {"remove_cafes": [str(ident)]}

    elif m_type == MutationType.ADD_PREFERENCE:
        cat = (command.target_name or command.target_id or "").strip().lower()
        return {"add_preferences": [{"category": cat, "value": command.value}]}

    elif m_type == MutationType.REMOVE_PREFERENCE:
        cat = (command.target_name or command.target_id or "").strip().lower()
        return {"remove_preferences": [{"category": cat, "value": command.value}]}

    else:
        raise ValueError(f"Unsupported mutation type: {m_type}")


def apply_mutation_command(
    state: TripState,
    command: Union[MutationCommand, Dict[str, Any]],
) -> TripChangeSet:
    """
    Applies a single resolved MutationCommand to a TripState instance
    via the canonical apply_trip_state_update engine.
    """
    if isinstance(command, dict):
        cmd = MutationCommand(**command)
    elif isinstance(command, MutationCommand):
        cmd = command
    else:
        raise TypeError(f"Expected MutationCommand or dict, got {type(command)}")

    updates = mutation_to_state_updates(cmd)
    return apply_trip_state_update(state, updates)


def apply_mutation_batch(
    state: TripState,
    batch: Union[MutationBatch, Dict[str, Any], List[Any]],
) -> TripChangeSet:
    """
    Applies an ordered collection of validated MutationCommands coherently
    to a TripState instance via the canonical apply_trip_state_update engine.
    Validates all commands before making any state updates to avoid partial corruption.
    """
    if isinstance(batch, MutationBatch):
        commands = batch.mutations
    elif isinstance(batch, dict):
        commands = MutationBatch(**batch).mutations
    elif isinstance(batch, list):
        commands = [
            c if isinstance(c, MutationCommand) else MutationCommand(**c)
            for c in batch
        ]
    else:
        raise TypeError(f"Expected MutationBatch, dict, or list, got {type(batch)}")

    if not commands:
        return TripChangeSet()

    combined_updates: Dict[str, Any] = {}
    for cmd in commands:
        cmd_updates = mutation_to_state_updates(cmd)
        for key, val in cmd_updates.items():
            if key in (
                "selected_places",
                "selected_restaurants",
                "selected_cafes",
                "remove_places",
                "remove_restaurants",
                "remove_cafes",
                "add_preferences",
                "remove_preferences",
            ):
                if key not in combined_updates:
                    combined_updates[key] = []
                combined_updates[key].extend(val if isinstance(val, list) else [val])
            else:
                combined_updates[key] = val

    return apply_trip_state_update(state, combined_updates)
