"""
Context Builder for Mandate Recovery Agent.
Assembles all relevant situational data for a given failure record:
- Failure metadata (amount, customer, decline reason, timestamps)
- Historical notification logs for the customer/mandate
- Past retry counts and execution attempts

CRITICAL SECURITY REQUIREMENT:
ground_truth_reason and ground_truth_action are strictly stripped out
and NEVER exposed in the generated agent context.
"""
from datetime import datetime
from typing import Any, Optional
from src.database import (
    get_failure_record_by_id,
    get_notifications_for_mandate,
    get_audit_logs_for_record,
)
from src.guardrails.compliance_checker import parse_iso_datetime


def build_agent_context(record_data: dict[str, Any]) -> dict[str, Any]:
    """
    Constructs a sanitized context payload for the agent reasoning loop.
    Guarantees zero leakage of ground-truth evaluation labels.
    """
    record_id = record_data.get("record_id", "")
    mandate_id = record_data.get("mandate_id", "")

    # Retrieve in-database notification history for this mandate
    db_notifs = get_notifications_for_mandate(mandate_id)
    all_notifs = list(record_data.get("prior_notifications", [])) + db_notifs

    # Deduplicate notifications by timestamp and type
    seen_keys = set()
    cleaned_notifs = []
    for n in all_notifs:
        key = (n.get("notification_type"), n.get("sent_at"))
        if key not in seen_keys:
            seen_keys.add(key)
            cleaned_notifs.append({
                "notification_type": n.get("notification_type"),
                "sent_at": n.get("sent_at"),
                "channel": n.get("channel", "mock_sms"),
                "message": n.get("message")
            })

    # Calculate hours since last Pre-Debit Notification (PDN)
    last_pdn_time = None
    hours_since_last_pdn = None
    for n in cleaned_notifs:
        if n.get("notification_type") == "pre_debit_notice":
            try:
                dt = parse_iso_datetime(str(n.get("sent_at")))
                if last_pdn_time is None or dt > last_pdn_time:
                    last_pdn_time = dt
            except Exception:
                pass

    if last_pdn_time:
        now = datetime.now()
        hours_since_last_pdn = round((now - last_pdn_time).total_seconds() / 3600.0, 1)

    # Sanitized context dictionary — strictly excluding ground-truth fields
    context = {
        "record_id": record_id,
        "mandate_id": mandate_id,
        "merchant_id": record_data.get("merchant_id", "mer_nbfc_001"),
        "customer_id": record_data.get("customer_id", ""),
        "customer_name": record_data.get("customer_name", ""),
        "amount_paise": record_data.get("amount_paise", 0),
        "amount_inr": record_data.get("amount_paise", 0) / 100.0,
        "decline_reason": record_data.get("decline_reason", "UNCLASSIFIED"),
        "decline_timestamp": record_data.get("decline_timestamp", ""),
        "prior_retry_count": record_data.get("prior_retry_count", 0),
        "mandate_start_date": record_data.get("mandate_start_date", ""),
        "mandate_end_date": record_data.get("mandate_end_date"),
        "prior_notifications": cleaned_notifs,
        "has_prior_pdn": last_pdn_time is not None,
        "last_pdn_timestamp": last_pdn_time.isoformat() if last_pdn_time else None,
        "hours_since_last_pdn": hours_since_last_pdn,
        "source": record_data.get("source", "synthetic")
    }

    # Explicit assertion against ground truth leakage
    assert "ground_truth_reason" not in context, "Security violation: ground_truth_reason leaked!"
    assert "ground_truth_action" not in context, "Security violation: ground_truth_action leaked!"

    return context


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """
    Format sanitized context into a readable markdown brief for the model.
    """
    pdn_status = (
        f"Yes (Sent {context['hours_since_last_pdn']} hours ago at {context['last_pdn_timestamp']})"
        if context["has_prior_pdn"]
        else "None recorded (No valid 24h pre-debit notice exists)"
    )

    return f"""### Mandate Failure Incident Report
- **Record ID**: {context['record_id']}
- **Mandate ID**: {context['mandate_id']}
- **Customer Name**: {context['customer_name']} (ID: {context['customer_id']})
- **Outstanding Amount**: ₹{context['amount_inr']:,.2f} ({context['amount_paise']} paise)
- **Decline Reason**: {context['decline_reason']}
- **Decline Timestamp**: {context['decline_timestamp']}
- **Prior Retries Executed**: {context['prior_retry_count']} of 3 permitted
- **Mandate Validity Window**: {context['mandate_start_date']} to {context['mandate_end_date'] or 'Ongoing'}
- **Pre-Debit Notification (PDN) Status**: {pdn_status}
- **Notification History Count**: {len(context['prior_notifications'])} logged communications
"""
