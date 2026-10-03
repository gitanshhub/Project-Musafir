import subprocess
import sys
import os
import time

NON_LIVE_SUITES = [
    "test_component1_food_state.py",
    "test_component1_mutation_commands.py",
    "test_component2_mutation_application.py",
    "test_component2_planner.py",
    "test_component2_to_8_food.py",
    "test_component3_anchored_places.py",
    "test_component3_dependency_invalidation.py",
    "test_component4_derived_state_freshness.py",
    "test_component4_multi_selection_rejection.py",
    "test_component5_state_update_api.py",
    "test_step10_1_state.py",
    "test_step10_2_tools.py",
    "test_step10_3_llm.py",
    "test_step10_4_agent_loop.py",
    "test_step10_5_agent_chat.py",
    "test_step12_5_conversation.py",
    "test_step12_6_fast_path.py",
    "test_step12_6_integration.py",
    "test_step12_7_natural_date_context.py",
    "test_step12_hotels.py",
    "test_step13_agentic_planning.py",
    "test_step14_milestone2_e2e.py",
    "test_step9_itinerary.py",
    "test_batch1_replanning.py",
    "test_batch1_e2e_replanning.py",
    "test_batch2_modifications.py",
    "test_batch2_e2e_modifications.py",
    "test_batch2_1_hardening.py",
    "test_batch3_feasibility.py",
    "test_batch3_conversational_e2e.py",
    "test_m3_2_first_turn.py",
    "test_m3_2_adversarial.py",
    "validate_phase_a.py",
    "validate_phases_b_to_e.py",
    "test_m3_2_design_fixes.py",
    "test_m3_2_llm_fallback.py",
    "test_context_turn_pipeline.py",
]


def main():
    print(f"Starting Project Musafir Full Regression ({len(NON_LIVE_SUITES)} non-live suites)...")
    python_exe = sys.executable
    passed = 0
    failed = []
    start_total = time.time()

    for idx, suite in enumerate(NON_LIVE_SUITES, start=1):
        suite_path = os.path.join(os.path.dirname(__file__), suite)
        t0 = time.time()
        res = subprocess.run(
            [python_exe, suite_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        elapsed = time.time() - t0
        if res.returncode == 0:
            passed += 1
            print(f"  [{idx:02d}/{len(NON_LIVE_SUITES)}] PASS: {suite} ({elapsed:.2f}s)")
        else:
            failed.append((suite, res.stdout, res.stderr))
            print(f"  [{idx:02d}/{len(NON_LIVE_SUITES)}] FAIL: {suite} ({elapsed:.2f}s)")

    total_elapsed = time.time() - start_total
    print("\n" + "=" * 60)
    print(f"REGRESSION RUN COMPLETE in {total_elapsed:.2f}s")
    print(f"PASSED: {passed}/{len(NON_LIVE_SUITES)}")
    print(f"FAILED: {len(failed)}")
    print("=" * 60)

    if failed:
        print("\nFAILURE DETAILS:")
        for suite, out, err in failed:
            print(f"\n--- {suite} ---")
            print(out[-1000:])
            print(err[-1000:])
        sys.exit(1)
    else:
        print(f"\nALL {len(NON_LIVE_SUITES)}/{len(NON_LIVE_SUITES)} NON-LIVE REGRESSION SUITES PASSED CLEANLY!")
        sys.exit(0)

if __name__ == "__main__":
    main()
