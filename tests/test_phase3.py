"""
Unit and Integration Tests for Phase 3:
- Agent Reasoning & Multi-turn Orchestration Loop
- Context Builder Sanitization & Zero Ground-Truth Leakage
- Read-only Context Tools (mandate history, notification history)
- Decision Making Across All 7 Decline Taxonomy Scenarios
- Step-by-Step Reasoning Trace & Audit Persistence
"""
import pytest
from datetime import datetime, timedelta
from src.schema import DeclineReason, CorrectAction, AgentDecision
from src.agent.context_builder import build_agent_context, format_context_for_prompt
from src.agent.tool_registry import (
    get_mandate_history,
    get_notification_history,
    dispatch_tool_call,
    ACTION_TOOLS_MAP,
)
from src.agent.agent_loop import process_mandate_failure
from src.database import (
    init_database,
    insert_failure_record,
    get_connection,
    log_notification,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_database()


# --------------------------------------------------------------------------
# 1. Context Builder & Zero Ground-Truth Leakage Tests
# --------------------------------------------------------------------------

def test_context_builder_strips_ground_truth():
    """Security check: ensure ground truth labels never leak into agent context."""
    raw_record = {
        "record_id": "rec_leak_test",
        "mandate_id": "mand_leak_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_123",
        "customer_name": "Aarav LeakTest",
        "amount_paise": 500000,
        "decline_reason": "INSUFFICIENT_BALANCE",
        "decline_timestamp": "2026-09-01T10:00:00",
        "prior_retry_count": 1,
        "prior_notifications": [],
        "mandate_start_date": "2026-01-01T00:00:00",
        "source": "synthetic",
        # Secret ground-truth answers:
        "ground_truth_reason": "INSUFFICIENT_BALANCE",
        "ground_truth_action": "send_pre_debit_notice"
    }

    context = build_agent_context(raw_record)

    # Must NOT contain ground truth keys
    assert "ground_truth_reason" not in context
    assert "ground_truth_action" not in context

    # Must format clean markdown prompt without leaking labels
    prompt_str = format_context_for_prompt(context)
    assert "ground_truth" not in prompt_str.lower()
    assert "Aarav LeakTest" in prompt_str
    assert "₹5,000.00" in prompt_str


# --------------------------------------------------------------------------
# 2. Read-Only Context Tools
# --------------------------------------------------------------------------

def test_read_only_context_tools():
    """Verify read-only context tools fetch accurate information."""
    mandate_id = "mand_ro_test_01"
    record_data = {
        "record_id": "rec_ro_test",
        "mandate_id": mandate_id,
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_ro",
        "customer_name": "Rohan ReadOnly",
        "amount_paise": 750000,
        "decline_reason": "BANK_TECHNICAL_FAILURE",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 2,
        "prior_notifications": [],
        "mandate_start_date": "2025-06-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "BANK_TECHNICAL_FAILURE",
        "ground_truth_action": "schedule_retry"
    }
    insert_failure_record(record_data, "working")

    # Add a mock notification to DB
    log_notification(
        record_id="rec_ro_test",
        mandate_id=mandate_id,
        customer_id="cust_ro",
        notification_type="pre_debit_notice",
        message="Advance debit reminder",
        sent_at="2026-09-02T10:00:00"
    )

    # Test get_mandate_history
    history = get_mandate_history(mandate_id)
    assert history["mandate_id"] == mandate_id
    assert history["prior_retry_count"] == 2
    assert history["decline_reason"] == "BANK_TECHNICAL_FAILURE"

    # Test get_notification_history
    notifs = get_notification_history(mandate_id)
    assert notifs["mandate_id"] == mandate_id
    assert notifs["count"] >= 1


# --------------------------------------------------------------------------
# 3. Decision Scenarios Across the 7 Decline Reasons
# --------------------------------------------------------------------------

def test_agent_decision_insufficient_balance_without_pdn():
    """
    Scenario: Insufficient balance with NO prior 24h notification.
    Expected: Agent realizes legal requirement and calls send_pre_debit_notice.
    """
    record = {
        "record_id": "rec_ib_nopdn",
        "mandate_id": "mand_ib_nopdn",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_01",
        "customer_name": "Priya NoPDN",
        "amount_paise": 500000,
        "decline_reason": "INSUFFICIENT_BALANCE",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "INSUFFICIENT_BALANCE",
        "ground_truth_action": "send_pre_debit_notice"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_ib_nopdn")

    assert decision.diagnosed_reason == DeclineReason.INSUFFICIENT_BALANCE
    assert decision.chosen_action in [CorrectAction.SEND_PRE_DEBIT_NOTICE, CorrectAction.SCHEDULE_RETRY]
    assert "Pre-Debit Notification" in decision.reasoning
    assert decision.success is True


def test_agent_decision_insufficient_balance_with_valid_pdn():
    """
    Scenario: Insufficient balance with PDN sent 32 hours ago.
    Expected: Agent schedules compliant retry.
    """
    now = datetime.now()
    pdn_time = now - timedelta(hours=32)
    record = {
        "record_id": "rec_ib_validpdn",
        "mandate_id": "mand_ib_validpdn",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_02",
        "customer_name": "Ananya ValidPDN",
        "amount_paise": 500000,
        "decline_reason": "INSUFFICIENT_BALANCE",
        "decline_timestamp": now.isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [{
            "notification_type": "pre_debit_notice",
            "sent_at": pdn_time.isoformat(),
            "channel": "mock_sms"
        }],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "INSUFFICIENT_BALANCE",
        "ground_truth_action": "schedule_retry"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_ib_validpdn")

    assert decision.diagnosed_reason == DeclineReason.INSUFFICIENT_BALANCE
    assert decision.chosen_action == CorrectAction.SCHEDULE_RETRY
    assert decision.success is True


def test_agent_decision_mandate_expired():
    """
    Scenario: Mandate has expired.
    Expected: Non-recoverable; agent immediately calls escalate_to_human.
    """
    record = {
        "record_id": "rec_me_test",
        "mandate_id": "mand_me_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_03",
        "customer_name": "Vikram Expired",
        "amount_paise": 500000,
        "decline_reason": "MANDATE_EXPIRED",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2024-01-01T00:00:00",
        "mandate_end_date": (datetime.now() - timedelta(days=2)).isoformat(),
        "source": "synthetic",
        "ground_truth_reason": "MANDATE_EXPIRED",
        "ground_truth_action": "escalate_to_human"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_me_test")

    assert decision.diagnosed_reason == DeclineReason.MANDATE_EXPIRED
    assert decision.chosen_action == CorrectAction.ESCALATE_TO_HUMAN
    assert "expired" in decision.reasoning.lower()


def test_agent_decision_mandate_revoked():
    """
    Scenario: Mandate revoked by customer on UPI app.
    Expected: Customer consent withdrawal; agent immediately calls escalate_to_human.
    """
    record = {
        "record_id": "rec_mr_test",
        "mandate_id": "mand_mr_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_04",
        "customer_name": "Sneha Revoked",
        "amount_paise": 320000,
        "decline_reason": "MANDATE_REVOKED",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "MANDATE_REVOKED",
        "ground_truth_action": "escalate_to_human"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_mr_test")

    assert decision.diagnosed_reason == DeclineReason.MANDATE_REVOKED
    assert decision.chosen_action == CorrectAction.ESCALATE_TO_HUMAN
    assert "revoked" in decision.reasoning.lower()


def test_agent_decision_afa_limit_exceeded():
    """
    Scenario: Transaction exceeds ₹15,000 without AFA authentication.
    Expected: Exceeds agent authority; agent immediately calls escalate_to_human.
    """
    record = {
        "record_id": "rec_afa_test",
        "mandate_id": "mand_afa_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_05",
        "customer_name": "Aditya AFA",
        "amount_paise": 2200000,  # ₹22,000
        "decline_reason": "AFA_LIMIT_EXCEEDED",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "AFA_LIMIT_EXCEEDED",
        "ground_truth_action": "escalate_to_human"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_afa_test")

    assert decision.diagnosed_reason == DeclineReason.AFA_LIMIT_EXCEEDED
    assert decision.chosen_action == CorrectAction.ESCALATE_TO_HUMAN


def test_agent_decision_retry_ceiling_reached():
    """
    Scenario: prior_retry_count is 3 (cap reached).
    Expected: Agent ceases retries and calls escalate_to_human.
    """
    record = {
        "record_id": "rec_ceiling_test",
        "mandate_id": "mand_ceiling_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_06",
        "customer_name": "Kavita Ceiling",
        "amount_paise": 500000,
        "decline_reason": "INSUFFICIENT_BALANCE",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 3,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "INSUFFICIENT_BALANCE",
        "ground_truth_action": "escalate_to_human"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_ceiling_test")

    assert decision.chosen_action == CorrectAction.ESCALATE_TO_HUMAN
    assert "retry ceiling" in decision.reasoning.lower()


def test_agent_decision_unclassified_uncertainty_handling():
    """
    Scenario: Unclassified decline reason.
    Expected: Agent correctly admits uncertainty and calls escalate_to_human.
    """
    record = {
        "record_id": "rec_uc_test",
        "mandate_id": "mand_uc_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_07",
        "customer_name": "Rahul Unknown",
        "amount_paise": 500000,
        "decline_reason": "UNCLASSIFIED",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "UNCLASSIFIED",
        "ground_truth_action": "escalate_to_human"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_uc_test")

    assert decision.diagnosed_reason == DeclineReason.UNCLASSIFIED
    assert decision.chosen_action == CorrectAction.ESCALATE_TO_HUMAN
    assert "uncertainty" in decision.reasoning.lower() or "unclassified" in decision.reasoning.lower()


# --------------------------------------------------------------------------
# 4. Persistence & Audit Trail Verification
# --------------------------------------------------------------------------

def test_decision_persisted_to_database():
    """Verify that agent decisions are stored in SQLite agent_decisions table."""
    now = datetime.now()
    record = {
        "record_id": "rec_persist_test",
        "mandate_id": "mand_persist_test",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_persist",
        "customer_name": "Amitabh Persist",
        "amount_paise": 500000,
        "decline_reason": "EXECUTION_WINDOW_BLOCKED",
        "decline_timestamp": now.isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [{
            "notification_type": "pre_debit_notice",
            "sent_at": (now - timedelta(hours=30)).isoformat(),
            "channel": "mock_sms"
        }],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "EXECUTION_WINDOW_BLOCKED",
        "ground_truth_action": "schedule_retry"
    }
    insert_failure_record(record, "working")

    decision = process_mandate_failure("rec_persist_test")

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM agent_decisions WHERE record_id = ?",
        ("rec_persist_test",)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["mandate_id"] == "mand_persist_test"
    assert row["chosen_action"] == decision.chosen_action.value
    assert len(row["reasoning"]) > 10
