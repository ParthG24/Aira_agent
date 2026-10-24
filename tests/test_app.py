"""Comprehensive offline unit tests for Aria voice agent.

Tests deterministic business logic, order normalizer, policy flags,
two-step cancellation, authorization (IDOR protection), session DB isolation,
and summary schema. Runs 100% offline with zero external API calls.
"""

import pytest
from app.schemas.order import Order
from app.schemas.summary import CallSummary, CustomerIntent, ResolutionStatus
from app.brand import INITIAL_ORDERS, compute_order_policy
from app.tools import (
    normalize_order_id,
    execute_get_order_details,
    execute_request_cancellation,
    execute_confirm_cancellation,
    execute_tool_call,
)


# Mock session state object for 2-step consent tracking
class MockSessionState:
    def __init__(self):
        self.pending_cancellation_order_id = None


# --- 1. Order ID Normalization ---
def test_order_id_normalization():
    assert normalize_order_id("101") == "ORD-101"
    assert normalize_order_id("ord 101") == "ORD-101"
    assert normalize_order_id("ORD-102") == "ORD-102"
    assert normalize_order_id("ord103") == "ORD-103"
    assert normalize_order_id("random text") == "RANDOM TEXT"
    assert normalize_order_id("") == ""


# --- 2. Policy Computation & Order Details ---
def test_order_details_and_return_policy():
    db = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}

    # ORD-102: Delivered 14 days ago -> Exceeds 7-day return policy
    res102 = execute_get_order_details("ORD-102", db)
    assert res102["found"] is True
    assert res102["status"] == "Delivered"
    assert res102["policy"]["is_returnable"] is False
    assert "exceeds 7-day" in res102["policy"]["return_notes"].lower()

    # ORD-101: Out for Delivery -> Not delivered yet
    res101 = execute_get_order_details("ORD-101", db)
    assert res101["found"] is True
    assert res101["policy"]["is_returnable"] is False

    # Unknown order
    res_none = execute_get_order_details("ORD-999", db)
    assert res_none["found"] is False


def test_authorization_and_idor_protection():
    db = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}

    # ORD-101 belongs to Priya Sharma
    # Authorized access when caller is Priya Sharma
    res_auth = execute_get_order_details("ORD-101", db, current_customer="Priya Sharma")
    assert res_auth["found"] is True
    assert res_auth["authorized"] is True

    # IDOR attempt: Caller is Rahul Verma requesting Priya Sharma's order ORD-101
    res_denied = execute_get_order_details("ORD-101", db, current_customer="Rahul Verma")
    assert res_denied["found"] is True
    assert res_denied["authorized"] is False
    assert "belongs to another customer" in res_denied["message"].lower()


def test_shipping_and_cod_policy_rules():
    # Exactly ₹499 pays ₹50 shipping fee; strictly above ₹499 is free
    order_499 = Order(order_id="TEST-1", product="Test", value=499, status="Processing", carrier_notes="")
    p499 = compute_order_policy(order_499)
    assert p499["shipping_fee"] == 50

    order_500 = Order(order_id="TEST-2", product="Test", value=500, status="Processing", carrier_notes="")
    p500 = compute_order_policy(order_500)
    assert p500["shipping_fee"] == 0

    # COD limit is ₹2,500
    order_2500 = Order(order_id="TEST-3", product="Test", value=2500, status="Processing", carrier_notes="")
    assert compute_order_policy(order_2500)["cod_eligible"] is True

    order_3000 = Order(order_id="TEST-4", product="Test", value=3000, status="Processing", carrier_notes="")
    assert compute_order_policy(order_3000)["cod_eligible"] is False


# --- 3. Two-Step Cancellation Rules ---
def test_two_step_cancellation_flow():
    db = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}
    session = MockSessionState()

    # Step 1: Request cancellation of ORD-103 (Processing)
    res_req = execute_request_cancellation("ORD-103", db, current_customer="Ananya Patel", session_state=session)
    assert res_req["success"] is True
    assert res_req["status"] == "CONFIRMATION_REQUIRED"
    assert session.pending_cancellation_order_id == "ORD-103"
    # Order is not cancelled yet!
    assert db["ORD-103"].status == "Processing"

    # Step 2: Confirm cancellation
    res_confirm = execute_confirm_cancellation("ORD-103", True, db, current_customer="Ananya Patel", session_state=session)
    assert res_confirm["success"] is True
    assert res_confirm["status"] == "Cancelled"
    assert db["ORD-103"].status == "Cancelled"
    assert session.pending_cancellation_order_id is None


def test_cancellation_confirmation_without_step1_fails():
    db = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}
    session = MockSessionState()

    # Trying to confirm without requesting Step 1 first fails
    res_confirm = execute_confirm_cancellation("ORD-103", True, db, session_state=session)
    assert res_confirm["success"] is False
    assert "No pending cancellation request" in res_confirm["message"]


def test_cancel_non_processing_order_fails():
    db = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}
    # ORD-101 is Out for Delivery
    res = execute_request_cancellation("ORD-101", db)
    assert res["success"] is False
    assert "refuse delivery at the doorstep" in res["message"].lower()
    assert db["ORD-101"].status == "Out for Delivery"


def test_database_session_isolation():
    # Mutating a call's isolated db copy does NOT mutate INITIAL_ORDERS
    db_session = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}
    session = MockSessionState()
    execute_request_cancellation("ORD-103", db_session, session_state=session)
    execute_confirm_cancellation("ORD-103", True, db_session, session_state=session)
    assert db_session["ORD-103"].status == "Cancelled"
    # Root INITIAL_ORDERS remains Processing
    assert INITIAL_ORDERS["ORD-103"].status == "Processing"


# --- 4. Post-Call Summary Schema ---
def test_summary_schema_validation():
    data = {
        "customer_intent": "ORDER_TRACKING",
        "order_id": "ORD-101",
        "resolution_status": "RESOLVED",
        "call_summary": "Customer checked status of ORD-101. Out for delivery today.",
    }
    summary = CallSummary.model_validate(data)
    assert summary.customer_intent == CustomerIntent.ORDER_TRACKING
    assert summary.order_id == "ORD-101"
    assert summary.resolution_status == ResolutionStatus.RESOLVED


# --- 5. FastAPI Endpoints (/health and /) ---
def test_health_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["agent"] == "Aria"
    assert data["brand"] == "Aura Skincare"


def test_root_serves_html():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Aria Support" in response.text
    assert "Aura Skincare" in response.text


# --- 6. TTS Voice Configuration & Sentence Splitter (100% Offline) ---
def test_tts_speaker_and_splitter():
    from app.services.tts import PRIMARY_SPEAKER, TARGET_LANGUAGE, SentenceSplitter
    
    # Strictly ensure Shreya is configured as the Sarvam voice
    assert PRIMARY_SPEAKER == "shreya"
    assert TARGET_LANGUAGE == "en-IN"

    # Test sentence streaming segmentation
    splitter = SentenceSplitter()
    s1 = splitter.process_token("Hello there! ")
    assert s1 == ["Hello there!"]

    s2 = splitter.process_token("I'm checking your order now. ")
    assert s2 == ["I'm checking your order now."]

    # Partial tokens
    assert splitter.process_token("Your package is ") == []
    assert splitter.process_token("arriving soon. ") == ["Your package is arriving soon."]

    # Flush remainder
    splitter.process_token("Thank you")
    assert splitter.flush() == ["Thank you"]


@pytest.mark.asyncio
async def test_sarvam_tts_payload_and_no_neerja(monkeypatch):
    from app.services import tts
    import json

    captured_requests = []

    class MockResponse:
        status_code = 200
        def json(self):
            import base64
            # return 100 bytes of dummy audio data
            dummy_b64 = base64.b64encode(b"RIFFdummydataWAVE").decode("ascii")
            return {"audios": [dummy_b64]}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json=None, headers=None):
            captured_requests.append({"url": url, "json": json, "headers": headers})
            return MockResponse()

    monkeypatch.setattr(tts, "get_sarvam_client", lambda: MockAsyncClient())

    audio = await tts.synthesize_sarvam("Hello world", api_key="test_sarvam_key")
    assert audio == b"RIFFdummydataWAVE"
    assert len(captured_requests) == 1

    req = captured_requests[0]
    assert req["url"] == "https://api.sarvam.ai/text-to-speech"
    assert req["headers"]["api-subscription-key"] == "test_sarvam_key"
    assert req["json"]["speaker"] == "shreya"
    assert req["json"]["target_language_code"] == "en-IN"
    assert req["json"]["inputs"] == ["Hello world"]

    # Test that setting SARVAM_MODEL environment variable injects into payload
    monkeypatch.setenv("SARVAM_MODEL", "bulbul:v1")
    await tts.synthesize_sarvam("Hello again", api_key="test_sarvam_key")
    assert len(captured_requests) == 2
    assert captured_requests[1]["json"]["model"] == "bulbul:v1"


def test_free_tier_architecture_groq_llm_and_sarvam_voice(monkeypatch):
    from app.services.agent import get_agent, AriaAgent
    from app.services.stt import get_stt, GroqSTT
    from app.services import tts

    # 1. Agent LLM strictly uses Groq (free tier)
    monkeypatch.setenv("GROQ_API_KEY", "test_groq_key")
    agent = get_agent()
    assert isinstance(agent, AriaAgent)
    assert agent.client is not None

    # 2. STT strictly uses Groq Whisper (free tier)
    stt = get_stt()
    assert isinstance(stt, GroqSTT)

    # 3. Voice (TTS) uses Sarvam with Shreya persona
    assert tts.PRIMARY_SPEAKER == "shreya"
    assert tts.SARVAM_URL == "https://api.sarvam.ai/text-to-speech"




