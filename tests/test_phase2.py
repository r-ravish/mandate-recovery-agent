"""
Unit and Integration Tests for Phase 2:
- Compliance Rules Enforcement (PDN 24h, NPCI Execution Windows, 3-Retry Cap)
- Five Core Tools (retry_scheduler, pre_debit_notifier, recovery_nudge, promise_to_pay, escalation)
- Guardrail Layer & Audit Trail Logging
- Structural Non-Actions Verification
"""
import pytest
from datetime import datetime, timedelta
from src.guardrails.compliance_checker import (
    is_within_blocked_window,
    adjust_to_next_permitted_window,
    check_pre_debit_notification,
    check_retry_ceiling,
    check_recoverability,
    validate_compliance,
)
from src.guardrails.audit_logger import execute_tool_with_logging
from src.tools import (
    schedule_retry,
    send_pre_debit_notice,
    send_recovery_nudge,
    log_promise_to_pay,
    escalate_to_human,
    ALL_TOOLS,
)
from src.database import (
    init_database,
    get_connection,
    get_audit_logs_for_record,
    get_notifications_for_mandate,
    insert_failure_record,
)


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure database tables exist before each test."""
    init_database()


# --------------------------------------------------------------------------
# 1. Rule 2: Execution Window Compliance Tests
# --------------------------------------------------------------------------

def test_npci_blocked_windows():
    """Verify detection of NPCI peak congestion blocked windows."""
    base_date = datetime(2026, 9, 10)

    # 10:00 AM - 1:00 PM blocked
    assert is_within_blocked_window(base_date.replace(hour=10, minute=0)) is True
    assert is_within_blocked_window(base_date.replace(hour=11, minute=30)) is True
    assert is_within_blocked_window(base_date.replace(hour=12, minute=59)) is True

    # 5:00 PM - 9:30 PM blocked
    assert is_within_blocked_window(base_date.replace(hour=17, minute=0)) is True
    assert is_within_blocked_window(base_date.replace(hour=19, minute=15)) is True
    assert is_within_blocked_window(base_date.replace(hour=21, minute=29)) is True

    # Permitted hours
    assert is_within_blocked_window(base_date.replace(hour=8, minute=30)) is False
    assert is_within_blocked_window(base_date.replace(hour=9, minute=59)) is False
    assert is_within_blocked_window(base_date.replace(hour=13, minute=15)) is False
    assert is_within_blocked_window(base_date.replace(hour=16, minute=45)) is False
    assert is_within_blocked_window(base_date.replace(hour=21, minute=31)) is False
    assert is_within_blocked_window(base_date.replace(hour=22, minute=0)) is False


def test_window_auto_adjustment():
    """Verify blocked times are automatically shifted to next compliant window."""
    base_date = datetime(2026, 9, 10)

    # Peak morning (10:30 AM) -> shifts to 1:05 PM (13:05)
    adjusted, was_adj = adjust_to_next_permitted_window(base_date.replace(hour=10, minute=30))
    assert was_adj is True
    assert adjusted.hour == 13 and adjusted.minute == 5

    # Peak evening (7:00 PM) -> shifts to 9:35 PM (21:35)
    adjusted_pm, was_adj_pm = adjust_to_next_permitted_window(base_date.replace(hour=19, minute=0))
    assert was_adj_pm is True
    assert adjusted_pm.hour == 21 and adjusted_pm.minute == 35

    # Permitted time -> untouched
    untouched, was_adj_un = adjust_to_next_permitted_window(base_date.replace(hour=14, minute=0))
    assert was_adj_un is False
    assert untouched.hour == 14 and untouched.minute == 0


# --------------------------------------------------------------------------
# 2. Rule 1: 24-Hour Pre-Debit Notification (PDN) Tests
# --------------------------------------------------------------------------

def test_pdn_check_missing():
    """Verify retry is blocked if no PDN was ever recorded."""
    target_retry = datetime(2026, 9, 15, 8, 30)
    passed, error = check_pre_debit_notification("mand_no_pdn", target_retry, record_context={})
    assert passed is False
    assert "No Pre-Debit Notification" in error


def test_pdn_check_too_recent():
    """Verify retry is blocked if PDN was sent less than 24 hours prior."""
    now = datetime(2026, 9, 15, 12, 0)
    # PDN sent only 5 hours ago
    pdn_time = now - timedelta(hours=5)
    context = {
        "prior_notifications": [{
            "notification_type": "pre_debit_notice",
            "sent_at": pdn_time.isoformat(),
            "channel": "mock_sms"
        }]
    }
    passed, error = check_pre_debit_notification("mand_too_recent", now, record_context=context)
    assert passed is False
    assert "minimum advance notice is 24 hours" in error


def test_pdn_check_compliant():
    """Verify retry is allowed when PDN was sent >= 24 hours prior."""
    now = datetime(2026, 9, 15, 14, 0)
    # PDN sent 28 hours ago
    pdn_time = now - timedelta(hours=28)
    context = {
        "prior_notifications": [{
            "notification_type": "pre_debit_notice",
            "sent_at": pdn_time.isoformat(),
            "channel": "mock_sms"
        }]
    }
    passed, error = check_pre_debit_notification("mand_compliant_pdn", now, record_context=context)
    assert passed is True
    assert error is None


# --------------------------------------------------------------------------
# 3. Rule 3: Retry Ceiling Tests (Max 3 retries)
# --------------------------------------------------------------------------

def test_retry_ceiling_enforcement():
    """Verify maximum 3 retries cap."""
    # Under limit
    ok, _ = check_retry_ceiling("mand_1", current_retry_count=0)
    assert ok is True
    ok, _ = check_retry_ceiling("mand_1", current_retry_count=2)
    assert ok is True

    # At limit -> blocked
    blocked, err = check_retry_ceiling("mand_1", current_retry_count=3)
    assert blocked is False
    assert "Retry ceiling of 3 attempts already reached" in err


# --------------------------------------------------------------------------
# 4. Recoverable vs Non-Recoverable Decline Reasons Tests
# --------------------------------------------------------------------------

def test_non_recoverable_decline_reasons():
    """Verify non-recoverable reasons cannot be retried."""
    for reason in ["MANDATE_EXPIRED", "MANDATE_REVOKED", "UNCLASSIFIED", "AFA_LIMIT_EXCEEDED"]:
        ok, err = check_recoverability(reason)
        assert ok is False
        assert "structurally non-recoverable" in err

    for rec_reason in ["INSUFFICIENT_BALANCE", "EXECUTION_WINDOW_BLOCKED", "BANK_TECHNICAL_FAILURE"]:
        ok, err = check_recoverability(rec_reason)
        assert ok is True
        assert err is None


# --------------------------------------------------------------------------
# 5. Core Tools Execution Tests
# --------------------------------------------------------------------------

def test_send_pre_debit_notice_tool():
    """Test send_pre_debit_notice logs notification and delivers receipt."""
    res = send_pre_debit_notice(
        record_id="rec_test_pdn",
        mandate_id="mand_pdn_01",
        customer_id="cust_01",
        customer_name="Aarav Sharma",
        amount_paise=500000,
        planned_debit_time="2026-09-12T08:00:00",
        channel="mock_sms"
    )
    assert res["success"] is True
    assert res["status"] == "dispatched"
    assert "notification_id" in res
    assert "Aarav Sharma" in res["message_preview"]

    # Verify DB entry
    notifs = get_notifications_for_mandate("mand_pdn_01")
    assert len(notifs) >= 1
    assert notifs[-1]["notification_type"] == "pre_debit_notice"


def test_recovery_nudge_spam_cap():
    """Verify recovery nudge respects max 2 per mandate cycle."""
    mandate_id = "mand_nudge_test"
    # Send 1st nudge -> succeeds
    res1 = send_recovery_nudge("rec_1", mandate_id, "cust_01", "Priya Patel", 500000)
    assert res1["success"] is True
    assert res1["nudge_sequence"] == 1

    # Send 2nd nudge -> succeeds
    res2 = send_recovery_nudge("rec_1", mandate_id, "cust_01", "Priya Patel", 500000)
    assert res2["success"] is True
    assert res2["nudge_sequence"] == 2

    # Attempt 3rd nudge through guardrail -> blocked
    is_ok, violations = validate_compliance(
        "send_recovery_nudge",
        {"mandate_id": mandate_id},
        {"mandate_id": mandate_id}
    )
    assert is_ok is False
    assert any("Maximum of 2 recovery nudges" in v for v in violations)


def test_promise_to_pay_tool():
    """Test promise to pay registration and validation."""
    future_date = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    res = log_promise_to_pay(
        record_id="rec_ptp",
        mandate_id="mand_ptp",
        customer_id="cust_02",
        promised_date=future_date,
        promised_amount_paise=450000,
        notes="Customer called stating salary delayed until 10th"
    )
    assert res["success"] is True
    assert res["status"] == "ptp_active"

    # Reject past date
    past_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    bad_res = log_promise_to_pay("rec_ptp", "mand_ptp", "cust_02", past_date)
    assert bad_res["success"] is False
    assert "cannot be in the past" in bad_res["error"]


def test_escalation_tool():
    """Test escalation tool creates structured ticket."""
    res = escalate_to_human(
        record_id="rec_esc_01",
        mandate_id="mand_esc_01",
        reason="Customer explicitly revoked UPI AutoPay permission on PhonePe",
        priority="high"
    )
    assert res["success"] is True
    assert res["status"] == "handed_off_to_human"
    assert res["priority"] == "high"
    assert "ticket_id" in res


# --------------------------------------------------------------------------
# 6. Audit Logger & Guardrail Gatekeeper Tests
# --------------------------------------------------------------------------

def test_guardrail_blocks_unsafe_retry_and_logs():
    """
    Simulate agent attempting schedule_retry on a non-recoverable case
    without prior PDN. Verify execution is refused AND recorded in audit trail.
    """
    record_context = {
        "record_id": "rec_audit_unsafe",
        "mandate_id": "mand_unsafe",
        "decline_reason": "MANDATE_EXPIRED",  # Non-recoverable!
        "prior_retry_count": 0,
        "prior_notifications": []
    }
    tool_args = {
        "record_id": "rec_audit_unsafe",
        "mandate_id": "mand_unsafe",
        "requested_retry_time": "2026-09-10T11:00:00",  # Also in blocked window!
        "reason_for_retry": "Agent hallucinated that expired mandates can be retried"
    }

    result = execute_tool_with_logging(
        tool_name="schedule_retry",
        tool_fn=schedule_retry,
        tool_args=tool_args,
        agent_reasoning="Trying to retry immediately",
        record_context=record_context
    )

    # 1. Action was intercepted and blocked
    assert result["success"] is False
    assert result["blocked_by_guardrail"] is True
    assert len(result["violations"]) >= 1

    # 2. Immutable audit log recorded the blocked attempt
    logs = get_audit_logs_for_record("rec_audit_unsafe")
    assert len(logs) >= 1
    latest = logs[-1]
    assert latest["compliance_check_passed"] == 0
    assert latest["tool_name"] == "schedule_retry"
    assert len(latest["compliance_violations"]) >= 1


def test_guardrail_allows_compliant_retry():
    """
    Simulate agent calling schedule_retry when all rules are met (valid PDN, recoverable).
    Verify execution succeeds and audit trail records compliance.
    """
    # Seed a failure record in DB first
    record_data = {
        "record_id": "rec_audit_safe",
        "mandate_id": "mand_safe_01",
        "merchant_id": "mer_nbfc_001",
        "customer_id": "cust_safe",
        "customer_name": "Deepika Pillai",
        "amount_paise": 500000,
        "decline_reason": "INSUFFICIENT_BALANCE",
        "decline_timestamp": datetime.now().isoformat(),
        "prior_retry_count": 0,
        "prior_notifications": [],
        "mandate_start_date": "2025-01-01T00:00:00",
        "source": "synthetic",
        "ground_truth_reason": "INSUFFICIENT_BALANCE",
        "ground_truth_action": "schedule_retry"
    }
    insert_failure_record(record_data, "working")

    # Send a valid PDN 30 hours prior
    now = datetime.now()
    pdn_time = now - timedelta(hours=30)
    record_context = {
        "record_id": "rec_audit_safe",
        "mandate_id": "mand_safe_01",
        "decline_reason": "INSUFFICIENT_BALANCE",
        "prior_retry_count": 0,
        "prior_notifications": [{
            "notification_type": "pre_debit_notice",
            "sent_at": pdn_time.isoformat(),
            "channel": "mock_sms"
        }]
    }

    # Request retry at permitted hour (8:30 AM tomorrow)
    tomorrow_permitted = (now + timedelta(days=1)).replace(hour=8, minute=30, second=0)
    tool_args = {
        "record_id": "rec_audit_safe",
        "mandate_id": "mand_safe_01",
        "requested_retry_time": tomorrow_permitted.isoformat(),
        "reason_for_retry": "Sufficient balance expected after salary credit"
    }

    result = execute_tool_with_logging(
        tool_name="schedule_retry",
        tool_fn=schedule_retry,
        tool_args=tool_args,
        agent_reasoning="Compliant retry scheduled after 24h PDN window",
        record_context=record_context
    )

    assert result["success"] is True
    assert result["retry_attempt_number"] == 1

    # Check audit log shows compliance passed
    logs = get_audit_logs_for_record("rec_audit_safe")
    assert len(logs) >= 1
    assert logs[-1]["compliance_check_passed"] == 1


# --------------------------------------------------------------------------
# 7. Structural Non-Actions Verification
# --------------------------------------------------------------------------

def test_structural_non_actions_absence():
    """
    Verify that hazardous tools are absent from the exposed tools dictionary.
    No mandate cancellation, amount changes, or creation allowed.
    """
    exposed_names = set(ALL_TOOLS.keys())
    assert "cancel_mandate" not in exposed_names
    assert "modify_amount" not in exposed_names
    assert "change_amount" not in exposed_names
    assert "create_mandate" not in exposed_names
    assert "update_bank_account" not in exposed_names
    assert len(exposed_names) == 5
