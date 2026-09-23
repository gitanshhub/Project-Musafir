"""
Project Musafir — Step 10.4 Agent Tool-Calling Loop Verification Suite

Tests 1-11: Mocked LLM responses (0 token consumption, 0 credits spent).
Test 12: Controlled live Gemma 4 test via OpenRouter.
"""

import os
import sys
import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Set up logging for test run
logging.basicConfig(level=logging.INFO)

# Ensure backend root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.llm.openrouter_client import LLMResponse, OpenRouterClient
from app.agent.loop import AgentLoop, AgentResult, AgentLoopError, format_state_context, parse_tool_arguments
from app.agent.state import TripState
from app.schemas.hotel import Hotel
from app.schemas.place import Place


def make_mock_client(responses: List[LLMResponse]) -> OpenRouterClient:
    """Creates a mock OpenRouterClient that returns a sequence of LLMResponses."""
    mock = MagicMock(spec=OpenRouterClient)
    mock.send_message.side_effect = responses
    return mock


# =====================================================================
# TEST 1: Final Response Without Tools
# =====================================================================
def test_final_response_without_tools():
    print("\n[Test 1] Testing Final Response Without Tools...")
    mock_res = LLMResponse(
        content="Jaipur is a great destination for a historical trip.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([mock_res])
    loop = AgentLoop(client=client)

    result = loop.run(user_message="Tell me about Jaipur.")

    assert result.response == "Jaipur is a great destination for a historical trip."
    assert result.tool_calls == []
    assert result.iterations == 1
    assert len(result.messages) == 3  # system, user, assistant
    print("  ✓ Test 1 Passed: Finished cleanly in 1 iteration without tool execution.")


# =====================================================================
# TEST 2: Single Tool Call Execution
# =====================================================================
def test_single_tool_call():
    print("\n[Test 2] Testing Single Tool Call...")
    # Iteration 1: Model requests search_places
    mock_res_1 = LLMResponse(
        content="",
        tool_calls=[{
            "id": "call_places_01",
            "type": "function",
            "function": {
                "name": "search_places",
                "arguments": json.dumps({"destination": "Jaipur", "query": "palaces", "limit": 3}),
            },
        }],
        model="google/gemma-4-26b-a4b-it:free",
    )
    # Iteration 2: Model gives final response
    mock_res_2 = LLMResponse(
        content="Here are 3 great palaces to visit in Jaipur: Hawa Mahal, City Palace, Amber Palace.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([mock_res_1, mock_res_2])

    mock_executor = MagicMock()
    mock_executor.return_value = {
        "success": True,
        "places": [{"name": "Hawa Mahal"}, {"name": "City Palace"}, {"name": "Amber Palace"}],
    }

    loop = AgentLoop(client=client, tool_executor=mock_executor)
    result = loop.run(user_message="Show me palaces in Jaipur.")

    assert result.iterations == 2
    assert result.tool_calls == ["search_places"]
    assert "Hawa Mahal" in result.response
    mock_executor.assert_called_once_with(
        "search_places",
        {"destination": "Jaipur", "query": "palaces", "limit": 3},
        api_key=None,
    )
    # Verify messages structure: system, user, assistant(call), tool(result), assistant(final)
    assert len(result.messages) == 5
    assert result.messages[2]["role"] == "assistant"
    assert result.messages[3]["role"] == "tool"
    assert result.messages[3]["tool_call_id"] == "call_places_01"
    print("  ✓ Test 2 Passed: Single tool executed and result injected into conversation.")


# =====================================================================
# TEST 3: Multiple Sequential Tool Calls
# =====================================================================
def test_sequential_tool_calls():
    print("\n[Test 3] Testing Multiple Sequential Tool Calls...")
    # Iteration 1: search_hotels
    r1 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_1", "function": {"name": "search_hotels", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    # Iteration 2: search_places
    r2 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_2", "function": {"name": "search_places", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    # Iteration 3: search_restaurants
    r3 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_3", "function": {"name": "search_restaurants", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    # Iteration 4: Final response
    r4 = LLMResponse(
        content="I have found your hotel, places, and restaurants for Jaipur.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2, r3, r4])

    mock_executor = MagicMock(return_value={"success": True})
    loop = AgentLoop(client=client, tool_executor=mock_executor)

    result = loop.run(user_message="Plan my entire trip to Jaipur.")

    assert result.iterations == 4
    assert result.tool_calls == ["search_hotels", "search_places", "search_restaurants"]
    assert mock_executor.call_count == 3
    print("  ✓ Test 3 Passed: 3 sequential tools executed across 4 iterations.")


# =====================================================================
# TEST 4: Multiple Tool Calls in One Response
# =====================================================================
def test_parallel_tool_calls_in_one_turn():
    print("\n[Test 4] Testing Multiple Tool Calls in Single Turn...")
    r1 = LLMResponse(
        content="",
        tool_calls=[
            {"id": "call_p", "function": {"name": "search_places", "arguments": '{"destination": "Jaipur"}'}},
            {"id": "call_r", "function": {"name": "search_restaurants", "arguments": '{"destination": "Jaipur"}'}},
        ],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="Found both places and restaurants.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])
    mock_executor = MagicMock(return_value={"success": True})

    loop = AgentLoop(client=client, tool_executor=mock_executor)
    result = loop.run(user_message="Find places and restaurants in Jaipur.")

    assert result.iterations == 2
    assert result.tool_calls == ["search_places", "search_restaurants"]
    assert mock_executor.call_count == 2
    # Verify tool results preserved distinct IDs
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == "call_p"
    assert tool_messages[1]["tool_call_id"] == "call_r"
    print("  ✓ Test 4 Passed: Multiple tool calls in single response executed with matching IDs.")


# =====================================================================
# TEST 5: Invalid Tool Arguments Handled Safely
# =====================================================================
def test_invalid_tool_arguments():
    print("\n[Test 5] Testing Invalid Tool Arguments...")
    r1 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_err", "function": {"name": "search_hotels", "arguments": '{"adults": "NOT_AN_INT"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="Could you please specify which city you want to find hotels in?",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])

    def mock_executor_with_type_error(name, args, api_key=None):
        raise TypeError("adults must be an integer")

    loop = AgentLoop(client=client, tool_executor=mock_executor_with_type_error)
    result = loop.run(user_message="Find hotels for invalid adults.")

    assert result.iterations == 2
    tool_msg = [m for m in result.messages if m.get("role") == "tool"][0]
    payload = json.loads(tool_msg["content"])
    assert payload["success"] is False
    assert "Tool execution error" in payload["error"]
    print("  ✓ Test 5 Passed: Tool argument TypeError was caught and fed safely to LLM.")


# =====================================================================
# TEST 6: Unknown Tool Safely Rejected
# =====================================================================
def test_unknown_tool_rejection():
    print("\n[Test 6] Testing Unknown Tool Handling...")
    r1 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_unk", "function": {"name": "teleport_user", "arguments": "{}"}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="I cannot teleport you, but I can help you plan your travel.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])

    # Default execute_tool rejects unknown tools
    loop = AgentLoop(client=client)
    result = loop.run(user_message="Teleport me to Jaipur.")

    assert result.iterations == 2
    tool_msg = [m for m in result.messages if m.get("role") == "tool"][0]
    payload = json.loads(tool_msg["content"])
    assert payload["success"] is False
    assert "Tool 'teleport_user' is not recognized" in payload["error"]
    print("  ✓ Test 6 Passed: Unknown tool rejected safely.")


# =====================================================================
# TEST 7: Malformed Tool JSON Handled Safely
# =====================================================================
def test_malformed_tool_json():
    print("\n[Test 7] Testing Malformed Tool JSON...")
    r1 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_bad_json", "function": {"name": "search_places", "arguments": '{"destination": "Jaipur"'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="Understood, let me try again.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])
    loop = AgentLoop(client=client)

    result = loop.run(user_message="Find places in Jaipur.")
    assert result.iterations == 2
    tool_msg = [m for m in result.messages if m.get("role") == "tool"][0]
    payload = json.loads(tool_msg["content"])
    assert payload["success"] is False
    assert "Malformed JSON in tool arguments" in payload["error"]
    print("  ✓ Test 7 Passed: Malformed JSON handled gracefully without crashing.")


# =====================================================================
# TEST 8: Tool Execution Error Recovery
# =====================================================================
def test_tool_execution_error_recovery():
    print("\n[Test 8] Testing Tool Execution Error Recovery...")
    r1 = LLMResponse(
        content="",
        tool_calls=[{"id": "call_fail", "function": {"name": "search_places", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="I encountered an issue fetching places, would you like to search for hotels instead?",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])

    def failing_executor(name, args, api_key=None):
        return {"success": False, "error": "SerpApi connection timeout"}

    loop = AgentLoop(client=client, tool_executor=failing_executor)
    result = loop.run(user_message="Find places.")

    assert result.iterations == 2
    assert "issue fetching places" in result.response
    print("  ✓ Test 8 Passed: Model recovered from tool error.")


# =====================================================================
# TEST 9: Maximum Iteration Cap Enforced
# =====================================================================
def test_maximum_iterations_limit():
    print("\n[Test 9] Testing Maximum Iterations Limit (8)...")
    infinite_tool_response = LLMResponse(
        content="",
        tool_calls=[{"id": "call_loop", "function": {"name": "search_places", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([infinite_tool_response] * 15)
    mock_executor = MagicMock(return_value={"success": True})

    loop = AgentLoop(client=client, tool_executor=mock_executor)

    try:
        loop.run(user_message="Keep searching forever.")
        assert False, "Should have raised AgentLoopError"
    except AgentLoopError as err:
        assert "Maximum tool-call iterations exceeded" in str(err)
        print(f"  ✓ Caught expected AgentLoopError: {err}")
    print("  ✓ Test 9 Passed: Hard limit prevented infinite loop.")


# =====================================================================
# TEST 10: State Context Injection
# =====================================================================
def test_state_context_injection():
    print("\n[Test 10] Testing State Context Injection...")
    state = TripState(
        destination="Jaipur",
        trip_start_date=date(2026, 10, 1),
        trip_end_date=date(2026, 10, 4),
        number_of_days=3,
        hotel_budget=5000.0,
        hotel_selection=Hotel(name="ITC Rajputana", latitude=26.9124, longitude=75.7873),
        travel_mode="driving",
        selected_places=[Place(name="Hawa Mahal", data_id="place_hawa_mahal", latitude=26.9239, longitude=75.8267)],
    )

    r = LLMResponse(
        content="I see you are staying at ITC Rajputana with a budget of ₹5000.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r])
    loop = AgentLoop(client=client)

    result = loop.run(user_message="What is my current itinerary plan?", trip_state=state)

    system_msg = result.messages[0]["content"]
    assert "Destination: Jaipur" in system_msg
    assert "ITC Rajputana" in system_msg
    assert "Hotel Budget: ₹5000.0/night" in system_msg
    assert "Hawa Mahal" in system_msg
    assert result.state_summary["destination"] == "Jaipur"
    print("  ✓ Test 10 Passed: TripState summary properly injected into prompt context.")


# =====================================================================
# TEST 11: Message Sequence Preservation
# =====================================================================
def test_message_sequence_preservation():
    print("\n[Test 11] Testing Message Sequence Preservation...")
    r1 = LLMResponse(
        content="Let me look up hotels.",
        tool_calls=[{"id": "c1", "function": {"name": "search_hotels", "arguments": '{"destination": "Jaipur"}'}}],
        model="google/gemma-4-26b-a4b-it:free",
    )
    r2 = LLMResponse(
        content="Here are the hotels.",
        tool_calls=None,
        model="google/gemma-4-26b-a4b-it:free",
    )
    client = make_mock_client([r1, r2])
    mock_executor = MagicMock(return_value={"success": True, "hotels": []})

    loop = AgentLoop(client=client, tool_executor=mock_executor)
    prior_messages = [
        {"role": "user", "content": "Hi, I am planning a vacation."},
        {"role": "assistant", "content": "Hello! Where would you like to go?"},
    ]

    result = loop.run(messages=prior_messages, user_message="I want to go to Jaipur.")

    roles = [m["role"] for m in result.messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "tool", "assistant"]
    print("  ✓ Test 11 Passed: Preserved conversation sequence: system -> user -> assistant -> user -> assistant -> tool -> assistant.")


# =====================================================================
# TEST 12: Controlled Live Gemma 4 Tool-Calling Test
# =====================================================================
def test_live_gemma_tool_calling():
    print("\n[Test 12] Testing Live Gemma 4 Tool-Calling on OpenRouter...")
    import dotenv
    dotenv.load_dotenv("backend/.env", override=True)
    key = os.getenv("OPENROUTER_API_KEY")
    if not key or not key.startswith("sk-or-"):
        print("  ⚠ OPENROUTER_API_KEY not configured. Skipping live test.")
        return

    client = OpenRouterClient()
    loop = AgentLoop(client=client)

    prompt = "What are 2 famous historical places to visit in Jaipur? Please use the search_places tool to find them."
    print(f"  Sending live prompt to Gemma 4: '{prompt}'")
    try:
        result = loop.run(user_message=prompt)
        print(f"  ✓ Live call succeeded in {result.iterations} iteration(s)!")
        print(f"  ✓ Executed tools: {result.tool_calls}")
        print(f"  ✓ Model answer excerpt: {result.response[:120]}...")
    except Exception as exc:
        print(f"  ⚠ Live call encountered note/exception: {type(exc).__name__}: {exc}")
        # Rate limit, policy, or quota note should not fail the build if mock tests passed
        if any(w in str(exc).lower() for w in ["rate limit", "429", "403", "policy", "quota", "provider error"]):
            print(f"  ✓ Upstream provider restriction handled gracefully: {exc}")
        else:
            raise


if __name__ == "__main__":
    print("=" * 65)
    print("PROJECT MUSAFIR — STEP 10.4 AGENT TOOL LOOP VERIFICATION")
    print("=" * 65)

    test_final_response_without_tools()
    test_single_tool_call()
    test_sequential_tool_calls()
    test_parallel_tool_calls_in_one_turn()
    test_invalid_tool_arguments()
    test_unknown_tool_rejection()
    test_malformed_tool_json()
    test_tool_execution_error_recovery()
    test_maximum_iterations_limit()
    test_state_context_injection()
    test_message_sequence_preservation()
    test_live_gemma_tool_calling()

    print("\n" + "=" * 65)
    print("ALL STEP 10.4 AGENT LOOP TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 65)
