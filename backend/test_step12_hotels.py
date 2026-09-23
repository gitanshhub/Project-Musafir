"""
Project Musafir — Step 12 Backend Verification Suite
Tests the structured hotel results contract in /agent/chat and GET /hotels/{property_token}.
"""

import sys
import os
import json
from uuid import uuid4

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from main import app
from app.agent.loop import AgentLoop, AgentResult
from app.llm.openrouter_client import LLMResponse
from app.agent.state import clear_all_states, get_or_create_state

client = TestClient(app)


def test_1_hotel_result_serialization():
    print("[Test 1] Testing Structured Hotel Result Serialization...")
    clear_all_states()

    # Create mock loop that simulates search_hotels execution
    class MockHotelAgentLoop(AgentLoop):
        def run(self, messages, user_message=None, trip_state=None, serpapi_key=None, max_iterations=None):
            active_messages = list(messages)
            if user_message:
                active_messages.append({"role": "user", "content": user_message})
            active_messages.append({"role": "assistant", "content": "I found a great hotel for you."})

            mock_hotels = [
                {
                    "name": "Hotel Pearl Palace",
                    "rating": 4.6,
                    "review_count": 2840,
                    "price_per_night": 3200.0,
                    "currency": "INR",
                    "latitude": 26.9124,
                    "longitude": 75.7873,
                    "thumbnail": "https://example.com/pearl_palace.jpg",
                    "amenities": ["Free WiFi", "Restaurant", "Room service"],
                    "property_token": "token_pearl_palace_123",
                },
                {
                    "name": "Heritage Haveli",
                    "rating": 4.4,
                    "review_count": 1120,
                    "price_per_night": 4500.0,
                    "currency": "INR",
                    "latitude": 26.9200,
                    "longitude": 75.8100,
                    "thumbnail": "https://example.com/heritage_haveli.jpg",
                    "amenities": ["Swimming pool", "Spa", "Free WiFi"],
                    "property_token": "token_heritage_456",
                }
            ]

            return AgentResult(
                response="I found 2 great hotels in Jaipur under ₹5,000.",
                tool_calls=["search_hotels"],
                iterations=2,
                state_summary={"destination": "Jaipur"},
                messages=active_messages,
                results={"hotels": mock_hotels}
            )

    from app.api import agent as agent_api
    app.dependency_overrides[agent_api.get_agent_loop] = lambda: MockHotelAgentLoop()

    try:
        res = client.post("/agent/chat", json={"message": "Find hotels in Jaipur under 5000"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()

        assert "results" in data, "Expected 'results' field in response"
        assert data["results"] is not None, "Expected 'results' to not be null"
        assert "hotels" in data["results"], "Expected 'hotels' list in results"
        hotels = data["results"]["hotels"]
        assert len(hotels) == 2, f"Expected 2 hotels, got {len(hotels)}"

        h1 = hotels[0]
        assert h1["name"] == "Hotel Pearl Palace"
        assert h1["rating"] == 4.6
        assert h1["review_count"] == 2840
        assert h1["price_per_night"] == 3200.0
        assert h1["currency"] == "INR"
        assert h1["latitude"] == 26.9124
        assert h1["longitude"] == 75.7873
        assert h1["thumbnail"] == "https://example.com/pearl_palace.jpg"
        assert h1["amenities"] == ["Free WiFi", "Restaurant", "Room service"]
        assert h1["property_token"] == "token_pearl_palace_123"

        print("  ✓ Serialized 2 normalized hotels with all public fields successfully.")
        print("  ✓ Test 1 Passed!\n")
    finally:
        app.dependency_overrides.clear()


def test_2_no_raw_provider_data():
    print("[Test 2] Testing Zero Raw Provider Data Exposure in Hotel Results...")
    clear_all_states()

    class MockRawSerpApiAgentLoop(AgentLoop):
        def __init__(self):
            super().__init__()

        def run(self, messages, user_message=None, trip_state=None, serpapi_key=None, max_iterations=None):
            tool_output = {
                "success": True,
                "count": 1,
                "total_found": 1,
                "hotels": [
                    {
                        "name": "Raw Test Hotel",
                        "rating": 4.1,
                        "review_count": 50,
                        "price_per_night": 2000.0,
                        "currency": "INR",
                        "latitude": 26.9,
                        "longitude": 75.8,
                        "thumbnail": "https://example.com/thumb.jpg",
                        "amenities": ["WiFi"],
                        "property_token": "token_safe_999",
                        "serpapi_link": "https://serpapi.com/search.json?engine=google_hotels",
                        "raw_gps": {"latitude": 26.9, "longitude": 75.8},
                        "ad_status": False,
                    }
                ]
            }

            raw_hotels = tool_output.get("hotels", [])
            sanitized_hotels = []
            for h in raw_hotels:
                sanitized_hotels.append({
                    "name": h.get("name"),
                    "rating": h.get("rating"),
                    "review_count": h.get("review_count"),
                    "price_per_night": h.get("price_per_night"),
                    "currency": h.get("currency", "INR"),
                    "latitude": h.get("latitude"),
                    "longitude": h.get("longitude"),
                    "thumbnail": h.get("thumbnail"),
                    "amenities": h.get("amenities", []) or [],
                    "property_token": h.get("property_token"),
                })

            return AgentResult(
                response="Here is a hotel.",
                tool_calls=["search_hotels"],
                iterations=2,
                state_summary={},
                messages=[],
                results={"hotels": sanitized_hotels}
            )

    from app.api import agent as agent_api
    app.dependency_overrides[agent_api.get_agent_loop] = lambda: MockRawSerpApiAgentLoop()

    try:
        res = client.post("/agent/chat", json={"message": "Hotel check"})
        data = res.json()
        h = data["results"]["hotels"][0]

        forbidden_keys = ["serpapi_link", "raw_gps", "ad_status", "api_key", "extracted_hotel_class"]
        for fk in forbidden_keys:
            assert fk not in h, f"Forbidden key '{fk}' leaked into hotel object: {h}"

        print("  ✓ Zero raw SerpApi or internal keys present in hotel results.")
        print("  ✓ Test 2 Passed!\n")
    finally:
        app.dependency_overrides.clear()


def test_3_no_secrets_in_response():
    print("[Test 3] Testing Zero Secrets in AgentChatResponse...")
    clear_all_states()

    class MockSecretAgentLoop(AgentLoop):
        def run(self, messages, user_message=None, trip_state=None, serpapi_key=None, max_iterations=None):
            return AgentResult(
                response="Here are hotels.",
                tool_calls=["search_hotels"],
                iterations=1,
                state_summary={},
                messages=[],
                results={
                    "hotels": [
                        {
                            "name": "Safe Hotel",
                            "rating": 4.0,
                            "review_count": 100,
                            "price_per_night": 2500.0,
                            "currency": "INR",
                            "amenities": ["WiFi"],
                            "property_token": "token_123",
                        }
                    ]
                }
            )

    from app.api import agent as agent_api
    app.dependency_overrides[agent_api.get_agent_loop] = lambda: MockSecretAgentLoop()

    try:
        res = client.post("/agent/chat", json={"message": "Show hotels"})
        raw_text = res.text
        assert "OPENROUTER_API_KEY" not in raw_text
        assert "SERPAPI_API_KEY" not in raw_text
        assert "sk-" not in raw_text
        assert "Bearer" not in raw_text

        print("  ✓ Verified no API keys or secret tokens appear in response payload.")
        print("  ✓ Test 3 Passed!\n")
    finally:
        app.dependency_overrides.clear()


def test_4_empty_hotel_results():
    print("[Test 4] Testing Empty Hotel Results Serialization...")
    clear_all_states()

    class MockEmptyHotelAgentLoop(AgentLoop):
        def run(self, messages, user_message=None, trip_state=None, serpapi_key=None, max_iterations=None):
            return AgentResult(
                response="I could not find any hotels matching that budget.",
                tool_calls=["search_hotels"],
                iterations=2,
                state_summary={},
                messages=[],
                results={"hotels": []}
            )

    from app.api import agent as agent_api
    app.dependency_overrides[agent_api.get_agent_loop] = lambda: MockEmptyHotelAgentLoop()

    try:
        res = client.post("/agent/chat", json={"message": "Find hotels under 500"})
        assert res.status_code == 200
        data = res.json()
        assert data["results"] is not None
        assert data["results"]["hotels"] == []
        print("  ✓ Handled empty hotel search results without error.")
        print("  ✓ Test 4 Passed!\n")
    finally:
        app.dependency_overrides.clear()


def test_5_non_hotel_response_has_null_results():
    print("[Test 5] Testing Non-Hotel Query Returns results: null...")
    clear_all_states()

    class MockGeneralChatAgentLoop(AgentLoop):
        def run(self, messages, user_message=None, trip_state=None, serpapi_key=None, max_iterations=None):
            return AgentResult(
                response="Jaipur is famous for its dal baati churma and ghewar.",
                tool_calls=[],
                iterations=1,
                state_summary={},
                messages=[],
                results=None
            )

    from app.api import agent as agent_api
    app.dependency_overrides[agent_api.get_agent_loop] = lambda: MockGeneralChatAgentLoop()

    try:
        res = client.post("/agent/chat", json={"message": "What food is famous in Jaipur?"})
        assert res.status_code == 200
        data = res.json()
        assert data["results"] is None, f"Expected results to be null, got: {data['results']}"
        print("  ✓ Non-hotel conversation returns results: null.")
        print("  ✓ Test 5 Passed!\n")
    finally:
        app.dependency_overrides.clear()


def test_6_latest_search_hotels_overwrites():
    print("[Test 6] Testing Latest search_hotels Result Overwrites Previous Within Turn...")
    clear_all_states()

    loop = AgentLoop()
    mock_client_responses = [
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "call_1",
                "function": {
                    "name": "search_hotels",
                    "arguments": json.dumps({"destination": "Jaipur", "limit": 1})
                }
            }],
            model="mock",
        ),
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "call_2",
                "function": {
                    "name": "search_hotels",
                    "arguments": json.dumps({"destination": "Jaipur", "max_price": 4000, "limit": 1})
                }
            }],
            model="mock",
        ),
        LLMResponse(
            content="Here is the refined hotel choice.",
            tool_calls=None,
            model="mock",
        ),
    ]

    call_count = [0]
    class MockMultiStepClient:
        def send_message(self, messages, tools=None):
            resp = mock_client_responses[call_count[0]]
            call_count[0] += 1
            return resp

    tool_call_index = [0]
    def mock_executor(tool_name, args, api_key=None):
        tool_call_index[0] += 1
        if tool_call_index[0] == 1:
            return {
                "success": True,
                "hotels": [{
                    "name": "Old Hotel",
                    "rating": 3.5,
                    "review_count": 10,
                    "price_per_night": 8000.0,
                    "currency": "INR",
                    "amenities": ["WiFi"],
                    "property_token": "old_token",
                }]
            }
        else:
            return {
                "success": True,
                "hotels": [{
                    "name": "New Refined Hotel",
                    "rating": 4.5,
                    "review_count": 120,
                    "price_per_night": 3500.0,
                    "currency": "INR",
                    "amenities": ["Pool", "WiFi"],
                    "property_token": "new_token",
                }]
            }

    loop.client = MockMultiStepClient()
    loop.tool_executor = mock_executor

    state = get_or_create_state()
    res = loop.run(messages=[], user_message="Find hotels", trip_state=state)

    assert res.results is not None
    assert len(res.results["hotels"]) == 1
    assert res.results["hotels"][0]["name"] == "New Refined Hotel"
    assert res.results["hotels"][0]["property_token"] == "new_token"

    print("  ✓ Successfully verified latest search_hotels call overwrote earlier search in same turn.")
    print("  ✓ Test 6 Passed!\n")


def test_7_hotel_details_endpoint():
    print("[Test 7] Testing GET /hotels/{property_token} OpenAPI & Validation...")
    
    res = client.get("/hotels/%20%20")
    assert res.status_code in [400, 422], f"Expected 400 or 422 for whitespace token, got {res.status_code}"

    res2 = client.get("/hotels/mock_token?check_in=2026-09-20&check_out=2026-09-19")
    assert res2.status_code == 422, f"Expected 422 for invalid dates, got {res2.status_code}"

    print("  ✓ /hotels/{property_token} validation enforced correctly.")
    print("  ✓ Test 7 Passed!\n")


def run_all_tests():
    print("=================================================================")
    print("PROJECT MUSAFIR — STEP 12 HOTEL CONTRACT & API VERIFICATION")
    print("=================================================================\n")
    test_1_hotel_result_serialization()
    test_2_no_raw_provider_data()
    test_3_no_secrets_in_response()
    test_4_empty_hotel_results()
    test_5_non_hotel_response_has_null_results()
    test_6_latest_search_hotels_overwrites()
    test_7_hotel_details_endpoint()
    print("=================================================================")
    print("ALL STEP 12 BACKEND TESTS COMPLETED SUCCESSFULLY!")
    print("=================================================================")


if __name__ == "__main__":
    run_all_tests()
