"""Deterministic turn execution through the shared mutation and travel services."""

from datetime import timedelta
from typing import Any, Dict, List

from app.agent.context import SessionState
from app.agent.dependencies import DerivedResource, get_invalidated_resources
from app.agent.planner import decide_next_planning_action
from app.agent.replanner import (
    ReplanningActionType,
    ReplanningController,
    execute_replanning_cycle,
)
from app.agent.state import PlanningStage
from app.agent.state_update import (
    apply_trip_state_update,
    get_next_active_question,
    is_broad_destination,
)
from app.agent.turn import InterpretedTurn, resolve_turn_references


def execute_turn(turn: InterpretedTurn, session: SessionState) -> Dict[str, Any]:
    state, context = session.trip_state, session.conversation_context
    turn = resolve_turn_references(turn, session)
    if turn.cancelled:
        context.pending_actions = []
        context.pending_search_preferences = {}
        context.pending_turn = None
        context.clear_active_question()
        return {"response": "Canceled.", "tool_calls": [], "results": None}
    if turn.clarification:
        context.pending_turn = turn.model_dump()
        return {"response": turn.clarification, "tool_calls": [], "results": None}
    context.pending_turn = None

    change = apply_trip_state_update(state, turn.updates)
    changed = set(change.changed_fields)
    invalidated = get_invalidated_resources(changed | set(change.invalidated_fields))
    if "destination" in changed:
        context.visible_hotels = []
        context.visible_places = []
        context.visible_restaurants = []
        context.visible_result_set_id = None
    else:
        if DerivedResource.HOTEL_DISCOVERY in invalidated:
            context.visible_hotels = []
        if DerivedResource.PLACE_DISCOVERY in invalidated:
            context.visible_places = []
        if DerivedResource.FOOD_DISCOVERY in invalidated:
            context.visible_restaurants = []

    actions = list(turn.requested_actions or context.pending_actions)
    if "PLAN_TRIP" in actions:
        planning_action = (
            "SEARCH_HOTELS"
            if state.hotel_search_required
            else "SEARCH_PLACES"
            if state.number_of_days
            else "PLAN_TRIP"
        )
        actions = list(
            dict.fromkeys(planning_action if a == "PLAN_TRIP" else a for a in actions)
        )
    if state.hotel_search_required is False:
        actions = [a for a in actions if a != "SEARCH_HOTELS"] or (
            ["SEARCH_PLACES"] if "SEARCH_HOTELS" in context.pending_actions else []
        )
    context.pending_actions = list(actions)
    pending_queue = context.pending_actions
    completed_searches: List[str] = []
    published_cards: Dict[str, Any] = {}
    if turn.requested_actions:
        context.pending_search_preferences = {
            field: getattr(turn, field)
            for field in ("food_category", "meal_type", "location_anchor")
            if getattr(turn, field) is not None
        }
    tools: List[str] = []
    results: Dict[str, Any] = {}
    replies = []
    if turn.updates:
        details = []
        if state.destination:
            details.append(state.destination)
        if state.number_of_days:
            details.append(
                f"{state.number_of_days} days ({state.number_of_nights or 0} nights)"
            )
        if state.trip_start_date and state.trip_end_date:
            details.append(
                f"{state.trip_start_date.strftime('%d %B')} to {state.trip_end_date.strftime('%d %B')} ({state.number_of_days} days, {state.number_of_nights or 0} nights)"
            )
        if state.trip_budget:
            details.append(f"₹{state.trip_budget:,.0f} whole-trip budget")
        if state.hotel_total_budget:
            details.append(f"₹{state.hotel_total_budget:,.0f} accommodation budget")
        if state.hotel_budget:
            details.append(f"₹{state.hotel_budget:,.0f}/night")
        replies.append("Got it" + (" — " + ", ".join(details) if details else "") + ".")

    for action in actions:
        question = get_next_active_question(state)
        if action in ("SEARCH_PLACES", "SEARCH_FOOD"):
            question = (
                question if question and question.field == "destination" else None
            )
        if action in ("ROUTE_REQUEST", "ITINERARY_REQUEST"):
            question = None
        if question:
            context.active_question = question
            replies.append(
                question.prompt_text or "What should I use for " + question.field + "?"
            )
            break
        if action == "SEARCH_HOTELS" and not state.number_of_nights:
            prompt = "How many nights do you need accommodation for?"
            context.set_active_question(
                field="number_of_nights",
                expected_type="number",
                scope="accommodation",
                prompt_text=prompt,
            )
            replies.append(prompt)
            break
        context.clear_active_question()
        if action in ("ROUTE_REQUEST", "ITINERARY_REQUEST"):
            plan = ReplanningController.decide(
                state=state, user_intent=action, change_set=change
            )
            if plan.action_type == ReplanningActionType.ASK:
                replies.append(
                    plan.prompt_message or "Please select the stops to include first."
                )
                break
            if plan.action_type == ReplanningActionType.ANSWER:
                replies.append("Your plan is already current.")
            else:
                outcome = execute_replanning_cycle(state, plan)
                tools.extend(outcome.get("executed_tools", []))
                if not outcome.get("success"):
                    replies.append(
                        "I saved your changes, but couldn't rebuild the plan. Please try again."
                    )
                    break
                if (
                    state.feasibility_result
                    and not state.feasibility_result.is_feasible
                ):
                    from app.agent.feasibility import format_feasibility_explanation

                    replies.append(
                        format_feasibility_explanation(state.feasibility_result, state)
                    )
                else:
                    replies.append(
                        "Your route is updated."
                        if action == "ROUTE_REQUEST"
                        else "Your daily itinerary is updated."
                    )
            if action == "ROUTE_REQUEST" and state.current_route:
                results["route"] = state.current_route.model_dump()
            if action == "ITINERARY_REQUEST" and state.current_itinerary:
                results["itinerary"] = state.current_itinerary.model_dump()
            if state.feasibility_result:
                results["feasibility"] = state.feasibility_result.model_dump()
        else:
            if not state.destination or is_broad_destination(state.destination):
                break
            from app.agent import tools as travel_tools

            if action == "SEARCH_HOTELS":
                if state.trip_start_date is None or state.number_of_nights is None:
                    replies.append(
                        "When are you starting, and how many nights do you need?"
                    )
                    break
                name, key, entity, stage = (
                    "search_hotels",
                    "hotels",
                    "hotel",
                    PlanningStage.HOTEL_SELECTION,
                )
                args: Dict[str, Any] = {
                    "destination": state.destination,
                    "check_in": state.trip_start_date.isoformat(),
                    "check_out": (
                        state.trip_start_date + timedelta(days=state.number_of_nights)
                    ).isoformat(),
                    "max_price": state.effective_hotel_budget,
                }
            elif action == "SEARCH_PLACES":
                name, key, entity, stage = (
                    "search_places",
                    "places",
                    "place",
                    PlanningStage.PLACE_SELECTION,
                )
                args = dict(
                    decide_next_planning_action(
                        state, context, "SEARCH_PLACES"
                    ).tool_args
                )
            elif action == "SEARCH_FOOD":
                name, key, entity, stage = (
                    "search_restaurants",
                    "restaurants",
                    "restaurant",
                    PlanningStage.FOOD_SELECTION,
                )
                args = dict(
                    decide_next_planning_action(state, context, "SEARCH_FOOD").tool_args
                )
                preferences = context.pending_search_preferences
                if preferences.get("food_category"):
                    args["category"] = preferences["food_category"]
                    args["query"] = (
                        "cafes"
                        if preferences["food_category"] == "cafe"
                        else "restaurants"
                    )
                if preferences.get("meal_type"):
                    args["meal_type"] = preferences["meal_type"]
                    args["query"] = (
                        preferences["meal_type"]
                        + " "
                        + args.get("query", "restaurants")
                    )
            else:
                break
            version = state.state_version
            anchor = context.pending_search_preferences.get("location_anchor")
            if anchor and action in ("SEARCH_PLACES", "SEARCH_FOOD"):
                if anchor.lower() == "hotel":
                    if not state.hotel_selection:
                        replies.append("Which hotel should I search near?")
                        break
                    anchor = state.hotel_selection.name
                matches: List[Any] = [
                    p
                    for p in state.selected_places
                    + state.selected_restaurants
                    + state.selected_cafes
                    if p.name.lower() == anchor.lower()
                ]
                if (
                    state.hotel_selection
                    and state.hotel_selection.name.lower() == anchor.lower()
                ):
                    matches = [state.hotel_selection]
                args["location_anchor"] = anchor
                args["query"] = args.get("query", "attractions") + " near " + anchor
                if matches:
                    args["latitude"], args["longitude"] = (
                        matches[0].latitude,
                        matches[0].longitude,
                    )
                else:
                    args.pop("latitude", None)
                    args.pop("longitude", None)
            tools.append(name)
            try:
                outcome = getattr(travel_tools, name + "_tool")(
                    **args, trip_state=state.model_copy(deep=True)
                )
            except Exception:
                outcome = {"success": False}
            if state.state_version != version:
                results.clear()
                for field, published in published_cards.items():
                    cards, resource = published
                    if getattr(context, field) is cards:
                        setattr(context, field, [])
                        state.mark_derived_stale(resource)
                if context.pending_actions is pending_queue:
                    context.pending_actions = completed_searches + pending_queue
                replies = [
                    "Your trip changed during the search. I'll use the latest preferences on the next attempt."
                ]
                break
            if not outcome.get("success"):
                replies.append(
                    "I saved your preferences, but the live search didn't complete. Please try again."
                )
                break
            resource = {
                "hotels": DerivedResource.HOTEL_DISCOVERY,
                "places": DerivedResource.PLACE_DISCOVERY,
                "restaurants": DerivedResource.FOOD_DISCOVERY,
            }[key]
            state.mark_derived_valid(resource, result_state_version=version)
            items = outcome.get(key, [])
            context.set_visible_items(entity, items)
            visible_field = f"visible_{key}"
            published_cards[visible_field] = (
                getattr(context, visible_field),
                resource,
            )
            completed_searches.append(action)
            state.planning_stage = stage
            results[key] = items
            replies.append(
                f"Here are {key} in {state.destination}. Choose the ones you want."
                if items
                else f"No matching {key} were found. We can try different preferences."
            )
        context.pending_actions.remove(action)
        context.last_agent_action = action

    if not actions:
        # Pure edits do not start unrelated discovery, but continue an existing question.
        if context.active_question:
            context.active_question = get_next_active_question(state)
            if context.active_question:
                replies.append(
                    context.active_question.prompt_text
                    or "What should I use for " + context.active_question.field + "?"
                )
    return {
        "response": " ".join(replies) or "Got it.",
        "tool_calls": tools,
        "results": results or None,
    }
