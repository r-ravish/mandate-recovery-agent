"""
Pre-Debit Notifier Tool for Mandate Recovery Agent.
Logs and simulates dispatch of the mandatory RBI/NPCI 24-hour advance
Pre-Debit Notification (PDN) to the customer before an auto-debit retry.
"""
from datetime import datetime
from typing import Any
from src.database import log_notification, get_notifications_for_mandate
from src.guardrails.compliance_checker import parse_iso_datetime


def send_pre_debit_notice(
    record_id: str,
    mandate_id: str,
    customer_id: str,
    customer_name: str,
    amount_paise: int,
    planned_debit_time: str,
    channel: str = "mock_sms"
) -> dict[str, Any]:
    """
    Simulates sending the legally required Pre-Debit Notification (PDN).
    
    Parameters:
    - record_id: Failure record identifier
    - mandate_id: Mandate identifier
    - customer_id: Customer identifier
    - customer_name: Customer's display name
    - amount_paise: Mandate debit amount in paise
    - planned_debit_time: Planned ISO timestamp of the upcoming debit retry
    - channel: Communication channel (mock_sms, mock_whatsapp, mock_email)
    
    Returns:
    - Receipt with notification_id, timestamp, and message preview
    """
    now = datetime.now()
    now_iso = now.isoformat()

    # Spam check: Avoid sending identical PDNs within 6 hours
    existing = get_notifications_for_mandate(mandate_id)
    recent_pdns = [
        n for n in existing
        if n.get("notification_type") == "pre_debit_notice"
    ]
    for p in recent_pdns:
        try:
            sent_dt = datetime.fromisoformat(p["sent_at"])
            if (now - sent_dt).total_seconds() < 6 * 3600:
                return {
                    "success": True,
                    "action": "send_pre_debit_notice",
                    "status": "already_notified",
                    "notification_id": p.get("notification_id"),
                    "mandate_id": mandate_id,
                    "message": "Valid pre-debit notice was already dispatched within the last 6 hours. Re-dispatch skipped."
                }
        except Exception:
            pass

    amount_inr = amount_paise / 100.0

    message_content = (
        f"Dear {customer_name}, your monthly loan EMI of ₹{amount_inr:,.2f} is scheduled "
        f"for auto-debit via UPI AutoPay on {planned_debit_time}. "
        f"Please ensure sufficient balance in your linked account. - NBFC Finance"
    )

    notif_id = log_notification(
        record_id=record_id,
        mandate_id=mandate_id,
        customer_id=customer_id,
        notification_type="pre_debit_notice",
        message=message_content,
        sent_at=now_iso,
        channel=channel
    )

    return {
        "success": True,
        "action": "send_pre_debit_notice",
        "status": "dispatched",
        "notification_id": notif_id,
        "mandate_id": mandate_id,
        "customer_id": customer_id,
        "channel": channel,
        "sent_at": now_iso,
        "planned_debit_time": planned_debit_time,
        "message_preview": message_content,
        "message": f"Pre-debit notice successfully sent via {channel}. 24-hour compliance window initialized."
    }
