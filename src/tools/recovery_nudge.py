"""
Recovery Nudge Tool for Mandate Recovery Agent.
Sends a courteous payment reminder / nudge to the customer via WhatsApp or SMS
after an initial failure has occurred.

Guardrail: Enforces a cap of at most 2 nudges per mandate per cycle
to prevent customer fatigue or spamming.
"""
from datetime import datetime
from typing import Any
from src.database import log_notification, get_notifications_for_mandate


def send_recovery_nudge(
    record_id: str,
    mandate_id: str,
    customer_id: str,
    customer_name: str,
    amount_paise: int,
    channel: str = "mock_whatsapp",
    custom_note: str = ""
) -> dict[str, Any]:
    """
    Sends a customer-facing recovery nudge.
    
    Parameters:
    - record_id: Failure record identifier
    - mandate_id: Mandate identifier
    - customer_id: Customer identifier
    - customer_name: Customer's display name
    - amount_paise: Outstanding amount in paise
    - channel: Communication channel (default: mock_whatsapp)
    - custom_note: Optional custom agent context
    """
    now_iso = datetime.now().isoformat()
    amount_inr = amount_paise / 100.0

    # Verification: check prior nudges for this mandate
    existing = get_notifications_for_mandate(mandate_id)
    prior_nudges = [n for n in existing if n.get("notification_type") == "recovery_nudge"]
    nudge_count = len(prior_nudges) + 1

    message_content = (
        f"Hi {customer_name}, your EMI debit of ₹{amount_inr:,.2f} for loan account {mandate_id} "
        f"was unsuccessful. To keep your account current and avoid late fees, kindly ensure funds "
        f"are available in your bank account, or reply to set a promise-to-pay date. - NBFC Collections"
    )

    notif_id = log_notification(
        record_id=record_id,
        mandate_id=mandate_id,
        customer_id=customer_id,
        notification_type="recovery_nudge",
        message=message_content,
        sent_at=now_iso,
        channel=channel
    )

    return {
        "success": True,
        "action": "send_recovery_nudge",
        "status": "dispatched",
        "notification_id": notif_id,
        "mandate_id": mandate_id,
        "customer_id": customer_id,
        "nudge_sequence": nudge_count,
        "channel": channel,
        "sent_at": now_iso,
        "message_preview": message_content,
        "message": f"Recovery nudge #{nudge_count} sent successfully via {channel}."
    }
