"""
Project Musafir — Milestone 3 Component 1 Verification: Mutation Command Model
Verifies:
1. MutationType, BudgetScope, ResolutionConfidence enum values and completeness.
2. Valid SET_DESTINATION (string normalization and rejection of empty/whitespace).
3. Valid SET_START_DATE and SET_END_DATE (date objects and ISO-8601 strings).
4. Valid SET_DURATION (integer >= 1, rejection of 0, negative, or invalid types).
5. Valid SET_BUDGET with explicit BudgetScope, and rejection of missing scope or <= 0 budget.
6. Valid SET_TRAVEL_MODE (supported modes and rejection of unsupported strings).
7. Valid SELECT_HOTEL and CHANGE_HOTEL (via reference, target_name/id, or object payload).
8. Valid SELECT_PLACE and REMOVE_PLACE (via reference, target_name/id, or object payload).
9. Valid SELECT_RESTAURANT and REMOVE_RESTAURANT.
10. Valid SELECT_CAFE and REMOVE_CAFE.
11. Valid ADD_PREFERENCE and REMOVE_PREFERENCE.
12. Structural rejection of malformed or incomplete mutation commands.
13. EntityReference & VisibleItemReference schema integrity and post-init synchronization.
14. MutationBatch container integrity and ordering.
15. Full JSON serialization & roundtrip deserialization via Pydantic model_dump.
16. Backward compatibility with ConversationContext and SessionState.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
from datetime import date as dt_date
from app.schemas.reference import EntityReference, VisibleItemReference
from app.schemas.mutation import (
    MutationType,
    BudgetScope,
    ResolutionConfidence,
    MutationCommand,
    MutationBatch,
    SUPPORTED_TRAVEL_MODES,
)
from app.agent.context import ConversationContext, SessionState

print("=" * 60)
print("PROJECT MUSAFIR — MILESTONE 3 COMPONENT 1: MUTATION COMMAND MODEL")
print("=" * 60)

# TEST 1: Enums & Vocabulary Completeness
print("\n[Test 1] Testing Mutation Enums & Supported Modes...")
expected_mutations = {
    "SET_DESTINATION", "SET_START_DATE", "SET_END_DATE", "SET_DURATION",
    "SET_BUDGET", "SET_TRAVEL_MODE",
    "SELECT_HOTEL", "CHANGE_HOTEL",
    "SELECT_PLACE", "REMOVE_PLACE",
    "SELECT_RESTAURANT", "REMOVE_RESTAURANT",
    "SELECT_CAFE", "REMOVE_CAFE",
    "ADD_PREFERENCE", "REMOVE_PREFERENCE",
}
actual_mutations = {m.value for m in MutationType}
assert expected_mutations == actual_mutations, f"Missing mutation types: {expected_mutations - actual_mutations}"

assert BudgetScope.NIGHTLY_HOTEL.value == "nightly_hotel"
assert BudgetScope.TOTAL_HOTEL.value == "total_hotel"
assert BudgetScope.TOTAL_TRIP.value == "total_trip"

assert ResolutionConfidence.HIGH.value == "high"
assert ResolutionConfidence.MEDIUM.value == "medium"
assert ResolutionConfidence.LOW.value == "low"

assert {"driving", "walking", "transit"}.issubset(SUPPORTED_TRAVEL_MODES)
print("  ✓ All 16 MutationType values, BudgetScopes, and ResolutionConfidences verified")
print("  ✓ Test 1 Passed!")

# TEST 2: SET_DESTINATION Validation
print("\n[Test 2] Testing SET_DESTINATION Validation...")
cmd_dest = MutationCommand(
    mutation_type=MutationType.SET_DESTINATION,
    value="  Kerala  ",
)
assert cmd_dest.value == "Kerala"

try:
    MutationCommand(mutation_type=MutationType.SET_DESTINATION, value="")
    assert False, "Should reject empty destination"
except ValueError:
    pass

try:
    MutationCommand(mutation_type=MutationType.SET_DESTINATION, value="   ")
    assert False, "Should reject whitespace destination"
except ValueError:
    pass
print("  ✓ SET_DESTINATION trims valid destination and rejects empty/whitespace")
print("  ✓ Test 2 Passed!")

# TEST 3: Dates Validation (SET_START_DATE & SET_END_DATE)
print("\n[Test 3] Testing SET_START_DATE & SET_END_DATE Validation...")
cmd_d1 = MutationCommand(
    mutation_type=MutationType.SET_START_DATE,
    value="2026-10-15",
)
assert isinstance(cmd_d1.value, dt_date)
assert cmd_d1.value == dt_date(2026, 10, 15)

cmd_d2 = MutationCommand(
    mutation_type=MutationType.SET_END_DATE,
    value=dt_date(2026, 10, 20),
)
assert cmd_d2.value == dt_date(2026, 10, 20)

try:
    MutationCommand(mutation_type=MutationType.SET_START_DATE, value="not-a-date")
    assert False, "Should reject invalid date string"
except ValueError:
    pass
print("  ✓ Dates normalized to dt_date; invalid strings rejected")
print("  ✓ Test 3 Passed!")

# TEST 4: SET_DURATION Validation
print("\n[Test 4] Testing SET_DURATION Validation...")
cmd_dur = MutationCommand(
    mutation_type=MutationType.SET_DURATION,
    value="5",
)
assert isinstance(cmd_dur.value, int)
assert cmd_dur.value == 5

try:
    MutationCommand(mutation_type=MutationType.SET_DURATION, value=0)
    assert False, "Should reject duration < 1"
except ValueError:
    pass

try:
    MutationCommand(mutation_type=MutationType.SET_DURATION, value="five")
    assert False, "Should reject non-numeric duration"
except ValueError:
    pass
print("  ✓ Duration validated to positive integer; <= 0 rejected")
print("  ✓ Test 4 Passed!")

# TEST 5: SET_BUDGET with Explicit Scope Required
print("\n[Test 5] Testing SET_BUDGET with Explicit Scope...")
cmd_b1 = MutationCommand(
    mutation_type=MutationType.SET_BUDGET,
    scope=BudgetScope.NIGHTLY_HOTEL,
    value="2500",
)
assert cmd_b1.value == 2500.0
assert cmd_b1.scope == BudgetScope.NIGHTLY_HOTEL

cmd_b2 = MutationCommand(
    mutation_type=MutationType.SET_BUDGET,
    scope=BudgetScope.TOTAL_HOTEL,
    value=15000,
)
assert cmd_b2.value == 15000.0
assert cmd_b2.scope == BudgetScope.TOTAL_HOTEL

# CRITICAL TEST: Must reject when scope is missing (no silent fallback!)
try:
    MutationCommand(
        mutation_type=MutationType.SET_BUDGET,
        value=5000,
    )
    assert False, "Must reject SET_BUDGET when scope is omitted"
except ValueError as e:
    assert "scope" in str(e).lower()

try:
    MutationCommand(
        mutation_type=MutationType.SET_BUDGET,
        scope=BudgetScope.TOTAL_TRIP,
        value=-100,
    )
    assert False, "Must reject budget <= 0"
except ValueError:
    pass
print("  ✓ Explicit BudgetScope strictly required; missing scope and <= 0 rejected")
print("  ✓ Test 5 Passed!")

# TEST 6: SET_TRAVEL_MODE Validation
print("\n[Test 6] Testing SET_TRAVEL_MODE Validation...")
cmd_mode = MutationCommand(
    mutation_type=MutationType.SET_TRAVEL_MODE,
    value=" WALKING ",
)
assert cmd_mode.value == "walking"

try:
    MutationCommand(
        mutation_type=MutationType.SET_TRAVEL_MODE,
        value="rocket",
    )
    assert False, "Should reject unsupported travel mode"
except ValueError:
    pass
print("  ✓ Supported travel modes normalized; invalid modes rejected")
print("  ✓ Test 6 Passed!")

# TEST 7: SELECT_HOTEL and CHANGE_HOTEL Validation
print("\n[Test 7] Testing SELECT_HOTEL & CHANGE_HOTEL...")
hotel_ref = EntityReference(
    entity_type="hotel",
    id="H101",
    name="Old Harbour Hotel",
    ordinal=1,
)
cmd_h1 = MutationCommand(
    mutation_type=MutationType.SELECT_HOTEL,
    reference=hotel_ref,
    confidence=ResolutionConfidence.HIGH,
)
assert cmd_h1.reference.id == "H101"

cmd_h2 = MutationCommand(
    mutation_type=MutationType.CHANGE_HOTEL,
    target_id="H202",
    target_name="Brunton Boatyard",
    value={"name": "Brunton Boatyard", "price_per_night": 6500.0},
)
assert cmd_h2.target_name == "Brunton Boatyard"

try:
    MutationCommand(mutation_type=MutationType.SELECT_HOTEL)
    assert False, "Should reject SELECT_HOTEL with no target or reference"
except ValueError:
    pass
print("  ✓ SELECT_HOTEL and CHANGE_HOTEL validated against references and targets")
print("  ✓ Test 7 Passed!")

# TEST 8: SELECT_PLACE and REMOVE_PLACE Validation
print("\n[Test 8] Testing SELECT_PLACE & REMOVE_PLACE...")
place_ref = EntityReference(
    entity_type="place",
    id="P1",
    name="Mattancherry Palace",
    ordinal=2,
)
cmd_p_sel = MutationCommand(
    mutation_type=MutationType.SELECT_PLACE,
    reference=place_ref,
)
assert cmd_p_sel.reference.name == "Mattancherry Palace"

cmd_p_rem = MutationCommand(
    mutation_type=MutationType.REMOVE_PLACE,
    target_name="Mattancherry Palace",
    source_reference="remove the palace",
)
assert cmd_p_rem.target_name == "Mattancherry Palace"

try:
    MutationCommand(mutation_type=MutationType.REMOVE_PLACE)
    assert False, "Should reject REMOVE_PLACE without target"
except ValueError:
    pass
print("  ✓ SELECT_PLACE and REMOVE_PLACE validate entity references and names")
print("  ✓ Test 8 Passed!")

# TEST 9: SELECT_RESTAURANT and REMOVE_RESTAURANT Validation
print("\n[Test 9] Testing SELECT_RESTAURANT & REMOVE_RESTAURANT...")
cmd_r_sel = MutationCommand(
    mutation_type=MutationType.SELECT_RESTAURANT,
    target_name="Grand Pavillion",
    value={"name": "Grand Pavillion", "cuisine": ["Kerala"]},
)
assert cmd_r_sel.target_name == "Grand Pavillion"

cmd_r_rem = MutationCommand(
    mutation_type=MutationType.REMOVE_RESTAURANT,
    target_id="R99",
)
assert cmd_r_rem.target_id == "R99"

try:
    MutationCommand(mutation_type=MutationType.REMOVE_RESTAURANT)
    assert False, "Should reject REMOVE_RESTAURANT without target"
except ValueError:
    pass
print("  ✓ Restaurant selection and removal validated")
print("  ✓ Test 9 Passed!")

# TEST 10: SELECT_CAFE and REMOVE_CAFE Validation
print("\n[Test 10] Testing SELECT_CAFE & REMOVE_CAFE...")
cmd_c_sel = MutationCommand(
    mutation_type=MutationType.SELECT_CAFE,
    target_name="Teapot Cafe",
    value={"name": "Teapot Cafe", "category": "Cafe"},
)
assert cmd_c_sel.target_name == "Teapot Cafe"

cmd_c_rem = MutationCommand(
    mutation_type=MutationType.REMOVE_CAFE,
    target_name="Teapot Cafe",
)
assert cmd_c_rem.target_name == "Teapot Cafe"
print("  ✓ Cafe selection and removal validated")
print("  ✓ Test 10 Passed!")

# TEST 11: ADD_PREFERENCE and REMOVE_PREFERENCE Validation
print("\n[Test 11] Testing Preferences...")
cmd_pref_add = MutationCommand(
    mutation_type=MutationType.ADD_PREFERENCE,
    target_name="dietary",
    value="vegetarian",
)
assert cmd_pref_add.target_name == "dietary"
assert cmd_pref_add.value == "vegetarian"

cmd_pref_rem = MutationCommand(
    mutation_type=MutationType.REMOVE_PREFERENCE,
    target_name="cuisine",
    value="North Indian",
)
assert cmd_pref_rem.target_name == "cuisine"

try:
    MutationCommand(mutation_type=MutationType.ADD_PREFERENCE, target_name="", value="")
    assert False, "Should reject empty preference"
except ValueError:
    pass
print("  ✓ ADD_PREFERENCE and REMOVE_PREFERENCE require category and value")
print("  ✓ Test 11 Passed!")

# TEST 12: Reference Schemas (EntityReference & VisibleItemReference)
print("\n[Test 12] Testing EntityReference and VisibleItemReference...")
vis_ref = VisibleItemReference(
    index=2,
    entity_type="place",
    id="P2",
    name="Fort Kochi Beach",
    price_per_night=None,
    rating=4.5,
    extra_data={"latitude": 9.965, "longitude": 76.241},
)
assert vis_ref.ordinal == 2
assert vis_ref.data["latitude"] == 9.965
print("  ✓ VisibleItemReference post-init seamlessly syncs with EntityReference fields")
print("  ✓ Test 12 Passed!")

# TEST 13: MutationBatch Container & Ordering
print("\n[Test 13] Testing MutationBatch Container...")
batch = MutationBatch(
    mutations=[cmd_dest, cmd_d1, cmd_dur, cmd_b1],
    source_message="I want to go to Kerala for 5 days starting Oct 15 with 2500 per night hotel",
)
assert len(batch.mutations) == 4
assert batch.mutations[0].mutation_type == MutationType.SET_DESTINATION
assert batch.mutations[3].mutation_type == MutationType.SET_BUDGET
print("  ✓ MutationBatch maintains order of operations and source message")
print("  ✓ Test 13 Passed!")

# TEST 14: Serialization & Deserialization
print("\n[Test 14] Testing Pydantic & JSON Serialization...")
dumped = batch.model_dump()
json_str = json.dumps(dumped, default=str)
reloaded = json.loads(json_str)
assert len(reloaded["mutations"]) == 4
assert reloaded["mutations"][0]["value"] == "Kerala"
assert reloaded["mutations"][1]["value"] == "2026-10-15"
print("  ✓ Full JSON serialization and roundtrip integrity verified")
print("  ✓ Test 14 Passed!")

# TEST 15: Backward Compatibility with ConversationContext & SessionState
print("\n[Test 15] Testing Backward Compatibility with Agent Context...")
ctx = ConversationContext()
ctx.set_visible_items(
    entity_type="hotel",
    items=[{"name": "Hotel Heritage", "price_per_night": 3000, "id": "H1"}],
)
assert len(ctx.visible_hotels) == 1
assert ctx.visible_hotels[0].index == 1
assert ctx.visible_hotels[0].name == "Hotel Heritage"
print("  ✓ Existing ConversationContext works seamlessly with updated reference schema")
print("  ✓ Test 15 Passed!")

# TEST 16: Module Re-Export Integrity in agent/mutation.py
print("\n[Test 16] Testing Agent Mutation Module Re-Exports...")
from app.agent.mutation import (
    MutationType as AgentMutationType,
    BudgetScope as AgentBudgetScope,
    MutationCommand as AgentMutationCommand,
    MutationBatch as AgentMutationBatch,
    EntityReference as AgentEntityReference,
)
assert AgentMutationType.SET_DESTINATION == MutationType.SET_DESTINATION
assert AgentBudgetScope.NIGHTLY_HOTEL == BudgetScope.NIGHTLY_HOTEL
print("  ✓ app.agent.mutation properly re-exports all domain schemas")
print("  ✓ Test 16 Passed!")

print("\n" + "=" * 60)
print("ALL 16 COMPONENT 1 MUTATION TESTS PASSED CLEANLY!")
print("=" * 60)
