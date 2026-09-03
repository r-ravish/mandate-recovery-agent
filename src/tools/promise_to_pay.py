"""
Promise to Pay (PTP) Logger Tool for Mandate Recovery Agent.
Records customer commitments to settle outstanding mandate debits by a specific date.
"""
from datetime import datetime, timedelta
from typing import Any, Optional
from src.guardrails.compliance_checker import parse_iso_datetime


def log_promise_to_pay(
    record_id: str,
    mandate_id: str,
    customer_id: str,
    promised_date: str,
    promised_amount_paise: Optional[int] = None,
    notes: str = "Customer requested delayed collection"
) -> dict[str, Any]:
    """
    Log a formal Promise to Pay (PTP) agreement.
    
    Parameters:
    - record_id: Failure record identifier
    - mandate_id: Mandate identifier
    - customer_id: Customer identifier
    - promised_date: Date by which customer promised funds will be available (ISO format)
    - promised_amount_paise: Optional specific amount pledged
    - notes: Context from customer conversation or reply
    """
    try:
        p_dt = parse_iso_datetime(promised_date)
    except Exception as e:
        return {
            "success": False,
            "error": f"Invalid promised_date format: {e}",
            "mandate_id": mandate_id
        }

    now = datetime.now()
    if p_dt < now - timedelta(days=1):
        return {
            "success": False,
            "error": "Promised date cannot be in the past.",
            "mandate_id": mandate_id
        }

    if p_dt > now + timedelta(days=35):
        return {
            "success": False,
            "error": "Promise to pay date cannot exceed 35 days (maximum loan cycle boundary).",
            "mandate_id": mandate_id
        }

    return {
        "success": True,
        "action": "log_promise_to_pay",
        "record_id": record_id,
        "mandate_id": mandate_id,
        "customer_id": customer_id,
        "promised_date": p_dt.strftime("%Y-%m-%d"),
        "promised_amount_paise": promised_amount_paise,
        "notes": notes,
        "status": "ptp_active",
        "message": f"Promise-to-pay registered for {p_dt.strftime('%d %b %Y')}. Mandate auto-retries suspended until promised date."
    }
