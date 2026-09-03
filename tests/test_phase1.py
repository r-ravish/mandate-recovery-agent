"""
Tests for Phase 1 Data Foundation:
- Schema validation
- Database initialization and CRUD
- Dataset generation & stratified splitting
- Razorpay webhook verification and ingestion
"""
import pytest
import json
from datetime import datetime
from fastapi.testclient import TestClient
from api.main import app
from src.schema import MandateFailureRecord, DeclineReason, CorrectAction
from src.database import get_failure_records, get_connection, init_database
from src.data.synthetic_generator import generate_single_record, determine_ground_truth_action
from src.webhook.razorpay_handler import map_razorpay_error_to_taxonomy

client = TestClient(app)


def test_database_init():
    """Verify tables exist in the SQLite database."""
    init_database()
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t["name"] for t in tables]
    conn.close()

    assert "mandate_failures" in table_names
    assert "audit_log" in table_names
    assert "agent_decisions" in table_names
    assert "notification_log" in table_names


def test_schema_record_generation():
    """Verify single record generation conforms to Pydantic schema."""
    now = datetime.now()
    record = generate_single_record(1, now, random_seed=42)
    assert isinstance(record, MandateFailureRecord)
    assert record.amount_paise > 0
    assert record.decline_reason in list(DeclineReason)
    assert record.ground_truth_action in list(CorrectAction)


def test_ground_truth_determination():
    """Verify ground truth actions adhere to compliance logic."""
    now = datetime.now()
    
    # 1. Expired mandate must escalate
    action = determine_ground_truth_action(
        DeclineReason.MANDATE_EXPIRED,
        prior_retries=0,
        prior_notifications=[],
        amount_paise=500000,
        decline_timestamp=now
    )
    assert action == CorrectAction.ESCALATE_TO_HUMAN

    # 2. Insufficient balance with prior retries >= 3 must escalate
    action = determine_ground_truth_action(
        DeclineReason.INSUFFICIENT_BALANCE,
        prior_retries=3,
        prior_notifications=[],
        amount_paise=500000,
        decline_timestamp=now
    )
    assert action == CorrectAction.ESCALATE_TO_HUMAN

    # 3. Insufficient balance with 0 retries and NO PDN must send pre-debit notice
    action = determine_ground_truth_action(
        DeclineReason.INSUFFICIENT_BALANCE,
        prior_retries=0,
        prior_notifications=[],
        amount_paise=500000,
        decline_timestamp=now
    )
    assert action == CorrectAction.SEND_PRE_DEBIT_NOTICE


def test_error_taxonomy_mapping():
    """Verify Razorpay errors map to correct taxonomy codes."""
    assert map_razorpay_error_to_taxonomy("BAD_REQUEST", "insufficient funds in account") == DeclineReason.INSUFFICIENT_BALANCE
    assert map_razorpay_error_to_taxonomy("GATEWAY_ERROR", "NPCI timed out peak window restricted") == DeclineReason.EXECUTION_WINDOW_BLOCKED
    assert map_razorpay_error_to_taxonomy("INTERNAL", "bank downtime error") == DeclineReason.BANK_TECHNICAL_FAILURE


def test_api_health():
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_api_records():
    """Test retrieving records by split."""
    response = client.get("/api/records?split=working")
    assert response.status_code == 200
    data = response.json()
    assert "records" in data
    assert data["count"] >= 1


def test_webhook_ingestion():
    """Test receiving a mock webhook from Razorpay."""
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_phase1_99",
                    "amount": 450000,
                    "subscription_id": "sub_test_p1",
                    "customer_id": "cust_p1_99",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "insufficient funds in bank account",
                    "error_reason": "payment_failed",
                    "notes": {"customer_name": "Test Webhook Customer"}
                }
            }
        }
    }
    response = client.post("/webhook/razorpay", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "processed"
    assert res_data["decline_reason"] == "INSUFFICIENT_BALANCE"
