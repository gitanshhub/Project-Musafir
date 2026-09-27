"""
Musafir M3.2 — Phase A: The Overfit & Paraphrase Validation Suite
Tests the extraction function (extract_trip_slots) directly against:
1. Paraphrases of the 21 benchmark inputs
2. A1 — Negation (mode rejection, hotel decline, dietary negation, disinterest)
3. A2 — Mid-sentence self-correction (duration, destination, mode)
4. A3 — Contrastive phrasing (rejected vs affirmed destination/mode)

Classifies every result:
- PASS
- MISSING_FIELD
- WRONG_VALUE
- WRONG_CONFIDENCE
- FALSE_POSITIVE
- DERIVATION_ERROR
- CRASH
"""

import sys
import os
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agent.extraction import extract_trip_slots, SlotConfidence
from app.agent.state import TripState
from app.agent.semantics import derive_trip_inferences


@dataclass
class TestCase:
    category: str
    input_text: str
    expected_fields: Dict[str, Any]
    forbidden_fields: Dict[str, Any] = None  # Fields or values that MUST NOT be present
    check_derivation: bool = False
    expected_derived: Dict[str, Any] = None


def run_phase_a():
    test_cases: List[TestCase] = [
        # -------------------------------------------------------------
        # Paraphrased Test 01: Kochi 1-day walking
        # -------------------------------------------------------------
        TestCase(
            category="Paraphrase_Test01",
            input_text="Kochi walking trip for one day",
            expected_fields={"destination": "Kochi", "number_of_days": 1, "travel_mode": "walking"},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": False},
        ),
        TestCase(
            category="Paraphrase_Test01",
            input_text="One day on foot exploring Kochi",
            expected_fields={"destination": "Kochi", "number_of_days": 1, "travel_mode": "walking"},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": False},
        ),
        TestCase(
            category="Paraphrase_Test01",
            input_text="I'll be walking around Kochi for a day",
            expected_fields={"destination": "Kochi", "number_of_days": 1, "travel_mode": "walking"},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": False},
        ),
        TestCase(
            category="Paraphrase_Test01",
            input_text="Kochi, 1 day, walking",
            expected_fields={"destination": "Kochi", "number_of_days": 1, "travel_mode": "walking"},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": False},
        ),

        # -------------------------------------------------------------
        # Paraphrased Test 02: Jaipur 3-day walking, veg, forts
        # -------------------------------------------------------------
        TestCase(
            category="Paraphrase_Test02",
            input_text="Jaipur for 3 days on foot, we are veg and love historical forts",
            expected_fields={
                "destination": "Jaipur",
                "number_of_days": 3,
                "travel_mode": "walking",
                "dietary_preferences": ["vegetarian"],
                "interests": ["forts"],
            },
            check_derivation=True,
            expected_derived={"number_of_nights": 2, "hotel_required": True},
        ),
        TestCase(
            category="Paraphrase_Test02",
            input_text="Visiting Jaipur 3-day walking vacation, interested in forts, vegetarian food only",
            expected_fields={
                "destination": "Jaipur",
                "number_of_days": 3,
                "travel_mode": "walking",
                "dietary_preferences": ["vegetarian"],
                "interests": ["forts"],
            },
            check_derivation=True,
            expected_derived={"number_of_nights": 2, "hotel_required": True},
        ),

        # -------------------------------------------------------------
        # Paraphrased Test 03: Manali 5 days 6 nights by car
        # -------------------------------------------------------------
        TestCase(
            category="Paraphrase_Test03",
            input_text="Drive to Manali for 5 days and 6 nights",
            expected_fields={"destination": "Manali", "number_of_days": 5, "number_of_nights": 6, "travel_mode": "driving"},
            check_derivation=True,
            expected_derived={"number_of_nights": 6, "hotel_required": True},
        ),
        TestCase(
            category="Paraphrase_Test03",
            input_text="Manali trip by car, 5 days, staying 6 nights",
            expected_fields={"destination": "Manali", "number_of_days": 5, "number_of_nights": 6, "travel_mode": "driving"},
            check_derivation=True,
            expected_derived={"number_of_nights": 6, "hotel_required": True},
        ),

        # -------------------------------------------------------------
        # Paraphrased Test 04: Day trip Agra by car with hotel override
        # -------------------------------------------------------------
        TestCase(
            category="Paraphrase_Test04",
            input_text="Driving to Agra for a single day, though I'll need a hotel stay",
            expected_fields={"destination": "Agra", "number_of_days": 1, "travel_mode": "driving", "hotel_required": True},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": True},
        ),
        TestCase(
            category="Paraphrase_Test04",
            input_text="Agra day trip by car, require a hotel though",
            expected_fields={"destination": "Agra", "number_of_days": 1, "travel_mode": "driving", "hotel_required": True},
            check_derivation=True,
            expected_derived={"number_of_nights": 0, "hotel_required": True},
        ),

        # -------------------------------------------------------------
        # Paraphrased Test 05 & 06: Missing or ambiguous duration
        # -------------------------------------------------------------
        TestCase(
            category="Paraphrase_Test05",
            input_text="Walking tour around Kochi",
            expected_fields={"destination": "Kochi", "travel_mode": "walking"},
            forbidden_fields={"number_of_days": None},
        ),
        TestCase(
            category="Paraphrase_Test06",
            input_text="Spending a couple of days in Goa",
            expected_fields={"destination": "Goa"},
            forbidden_fields={"number_of_days": None},
        ),

        # -------------------------------------------------------------
        # A1 — Negation
        # -------------------------------------------------------------
        TestCase(
            category="A1_Negation_Mode",
            input_text="I want to visit Jaipur for 3 days, I don't want to drive, I'll walk",
            expected_fields={"destination": "Jaipur", "number_of_days": 3, "travel_mode": "walking"},
            forbidden_fields={"travel_mode": "driving"},
        ),
        TestCase(
            category="A1_Negation_Mode",
            input_text="Planning a 2-day trip to Agra, I won't be walking",
            expected_fields={"destination": "Agra", "number_of_days": 2},
            forbidden_fields={"travel_mode": "walking"},
        ),
        TestCase(
            category="A1_Negation_Hotel",
            input_text="3 days in Jaipur, no hotel needed",
            expected_fields={"destination": "Jaipur", "number_of_days": 3, "hotel_required": False},
            check_derivation=True,
            expected_derived={"hotel_required": False},
        ),
        TestCase(
            category="A1_Negation_Hotel",
            input_text="Trip to Manali for 4 days, I don't need a hotel",
            expected_fields={"destination": "Manali", "number_of_days": 4, "hotel_required": False},
            check_derivation=True,
            expected_derived={"hotel_required": False},
        ),
        TestCase(
            category="A1_Negation_Diet",
            input_text="Jaipur 2 days, I'm vegetarian, not vegan",
            expected_fields={"destination": "Jaipur", "number_of_days": 2, "dietary_preferences": ["vegetarian"]},
            forbidden_fields={"dietary_preferences": "vegan"},
        ),
        TestCase(
            category="A1_Negation_Interest",
            input_text="3 days in Jaipur, interested in forts, but I don't care about beaches",
            expected_fields={"destination": "Jaipur", "number_of_days": 3, "interests": ["forts"]},
            forbidden_fields={"interests": "beaches"},
        ),

        # -------------------------------------------------------------
        # A2 — Mid-sentence self-correction
        # -------------------------------------------------------------
        TestCase(
            category="A2_Correction_Duration",
            input_text="3 days in Jaipur, actually 4 days",
            expected_fields={"destination": "Jaipur", "number_of_days": 4},
            forbidden_fields={"number_of_days": 3},
        ),
        TestCase(
            category="A2_Correction_Duration",
            input_text="3 days in Jaipur, actually 4",
            expected_fields={"destination": "Jaipur", "number_of_days": 4},
            forbidden_fields={"number_of_days": 3},
        ),
        TestCase(
            category="A2_Correction_Destination",
            input_text="Goa, no sorry, Kerala for 3 days",
            expected_fields={"destination": "Kerala", "number_of_days": 3},
            forbidden_fields={"destination": "Goa"},
        ),
        TestCase(
            category="A2_Correction_Mode",
            input_text="I'm driving — actually I'll rent a bike in Goa",
            expected_fields={"destination": "Goa", "travel_mode": "two_wheeler"},
            forbidden_fields={"travel_mode": "driving"},
        ),

        # -------------------------------------------------------------
        # A3 — Contrastive Phrasing
        # -------------------------------------------------------------
        TestCase(
            category="A3_Contrastive_Destination",
            input_text="I'm thinking about Goa, but I've decided on Jaipur for 3 days",
            expected_fields={"destination": "Jaipur", "number_of_days": 3},
            forbidden_fields={"destination": "Goa"},
        ),
        TestCase(
            category="A3_Contrastive_Mode",
            input_text="I usually travel by car, but this time I'm walking around Kochi for a day",
            expected_fields={"destination": "Kochi", "number_of_days": 1, "travel_mode": "walking"},
            forbidden_fields={"travel_mode": "driving"},
        ),
    ]

    print(f"\n============================================================")
    print(f"MUSAFIR M3.2 — PHASE A VALIDATION ({len(test_cases)} TEST CASES)")
    print(f"============================================================\n")

    results_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    failure_counts = {
        "MISSING_FIELD": 0,
        "WRONG_VALUE": 0,
        "WRONG_CONFIDENCE": 0,
        "FALSE_POSITIVE": 0,
        "DERIVATION_ERROR": 0,
        "CRASH": 0,
    }
    passed_count = 0

    for idx, tc in enumerate(test_cases, 1):
        test_id = f"[{idx:02d}/{len(test_cases):02d}] {tc.category}"
        try:
            res = extract_trip_slots(tc.input_text)
        except Exception as e:
            print(f"{test_id}: CRASH -> {e}")
            failure_counts["CRASH"] += 1
            continue

        failed = False
        failure_type = None
        failure_msg = ""

        # Check expected fields
        for field, exp_val in tc.expected_fields.items():
            act_val = getattr(res, field, None)
            if act_val is None:
                failed = True
                failure_type = "MISSING_FIELD"
                failure_msg = f"Expected {field}={exp_val!r}, but field was missing."
                break
            elif act_val != exp_val:
                failed = True
                failure_type = "WRONG_VALUE"
                failure_msg = f"Expected {field}={exp_val!r}, got {act_val!r}."
                break
            # Confidence check
            conf = res.confidence.get(field)
            if conf != SlotConfidence.HIGH:
                failed = True
                failure_type = "WRONG_CONFIDENCE"
                failure_msg = f"Field {field} confidence is {conf}, expected HIGH."
                break

        # Check forbidden fields / values (false positives)
        if not failed and tc.forbidden_fields:
            for field, forb_val in tc.forbidden_fields.items():
                act_val = getattr(res, field, None)
                if forb_val is None:
                    # Forbidden to exist at all in explicit_fields
                    if field in res.explicit_fields:
                        failed = True
                        failure_type = "FALSE_POSITIVE"
                        failure_msg = f"Field {field} should NOT be in explicit_fields, but got {act_val!r}."
                        break
                else:
                    if isinstance(act_val, list):
                        if forb_val in act_val:
                            failed = True
                            failure_type = "FALSE_POSITIVE"
                            failure_msg = f"Forbidden value {forb_val!r} found in {field}: {act_val!r}."
                            break
                    elif act_val == forb_val:
                        failed = True
                        failure_type = "FALSE_POSITIVE"
                        failure_msg = f"Field {field} has forbidden value {forb_val!r}."
                        break

        # Check derivation if requested
        if not failed and tc.check_derivation:
            high_updates = res.high_confidence_updates()
            st = TripState(**high_updates)
            explicit_keys = set(res.explicit_fields)
            derive_trip_inferences(st, explicit_fields=explicit_keys)
            for d_field, exp_d_val in tc.expected_derived.items():
                act_d_val = getattr(st, d_field, None)
                if act_d_val != exp_d_val:
                    failed = True
                    failure_type = "DERIVATION_ERROR"
                    failure_msg = f"Derived field {d_field}={act_d_val!r}, expected {exp_d_val!r}."
                    break

        if failed:
            failure_counts[failure_type] += 1
            print(f"FAIL: {test_id}")
            print(f"      Input:    \"{tc.input_text}\"")
            print(f"      Type:     {failure_type}")
            print(f"      Details:  {failure_msg}")
        else:
            passed_count += 1
            print(f"PASS: {test_id} -> \"{tc.input_text[:45]}...\"")

    print("\n============================================================")
    print(f"PHASE A SUMMARY: {passed_count}/{len(test_cases)} PASSED ({(passed_count/len(test_cases))*100:.1f}%)")
    print(f"FAILURES BY CATEGORY:")
    for ftype, count in failure_counts.items():
        print(f"  - {ftype:18s}: {count}")
    print("============================================================\n")

    return passed_count == len(test_cases)


if __name__ == "__main__":
    success = run_phase_a()
    sys.exit(0 if success else 1)
