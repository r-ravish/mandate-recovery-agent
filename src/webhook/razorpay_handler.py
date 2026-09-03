"""
Razorpay Webhook Handler for Mandate Recovery Agent.
Processes incoming webhook events from Razorpay Test Mode:
- Verifies HMAC SHA256 cryptographic signature
- Parses payment.failed and subscription failure payloads
- Maps Razorpay decline reasons to our 7-reason taxonomy
- Persists valid failure records to SQLite database
"""
import hmac
import hashlib
import json
from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, Request, HTTPException, Header, status
from src.config import RAZORPAY_WEBHOOK_SECRET
from src.schema import DeclineReason, CorrectAction, MandateFailureRecord
from src.database import insert_failure_record
from src.data.synthetic_generator import determine_ground_truth_action

router = APIRouter(prefix="/webhook", tags=["webhooks"])


def verify_razorpay_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """
    Verify the X-Razorpay-Signature HMAC SHA-256 header against the webhook secret.
    If no secret is configured (dev/mock mode), skips verification with a logged warning.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        # Development fallback if secret not yet generated in dashboard
        return True

    if not signature:
        return False

    expected_signature = hmac.new(
        key=RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)


def map_razorpay_error_to_taxonomy(error_code: str, error_description: str) -> DeclineReason:
    """
    Translate Razorpay API / gateway error descriptions into our 7 decline taxonomy reasons.
    """
    text = f"{error_code} {error_description}".lower()

    if any(k in text for k in ["insufficient", "balance", "funds", "low balance", "debit_amount_exceeded"]):
        return DeclineReason.INSUFFICIENT_BALANCE
    elif any(k in text for k in ["expired", "validity", "mandate_expired"]):
        return DeclineReason.MANDATE_EXPIRED
    elif any(k in text for k in ["window", "peak", "execution_window", "timing", "congestion"]):
        return DeclineReason.EXECUTION_WINDOW_BLOCKED
    elif any(k in text for k in ["afa", "limit", "max_amount", "threshold", "exceeded"]):
        return DeclineReason.AFA_LIMIT_EXCEEDED
    elif any(k in text for k in ["revoked", "cancelled", "customer_revoke", "paused"]):
        return DeclineReason.MANDATE_REVOKED
    elif any(k in text for k in ["technical", "gateway", "bank_downtime", "npci_timeout", "internal"]):
        return DeclineReason.BANK_TECHNICAL_FAILURE
    else:
        return DeclineReason.UNCLASSIFIED


@router.post("/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None, alias="X-Razorpay-Signature")
):
    """
    Endpoint receiving Razorpay webhooks for recurring subscription and payment failures.
    """
    raw_body = await request.body()

    # 1. Verify signature
    if not verify_razorpay_signature(raw_body, x_razorpay_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature. Request rejected."
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload.")

    event_type = payload.get("event", "")

    # We specifically handle payment failures on recurring mandates / subscriptions
    if event_type in ["payment.failed", "subscription.pending", "subscription.halted"]:
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        error_code = payment_entity.get("error_code", "")
        error_desc = payment_entity.get("error_description", "")
        error_reason = payment_entity.get("error_reason", "")

        decline_reason = map_razorpay_error_to_taxonomy(
            f"{error_code} {error_reason}",
            error_desc
        )

        amount_paise = payment_entity.get("amount", 500000)
        payment_id = payment_entity.get("id", f"pay_{int(datetime.now().timestamp())}")
        mandate_id = payment_entity.get("subscription_id") or payment_entity.get("order_id") or f"sub_{payment_id}"
        customer_id = payment_entity.get("customer_id", f"cust_{payment_id[-6:]}")
        customer_name = payment_entity.get("notes", {}).get("customer_name", "Sandbox Customer")

        now = datetime.now()
        ground_truth_action = determine_ground_truth_action(
            decline_reason,
            prior_retries=0,
            prior_notifications=[],
            amount_paise=amount_paise,
            decline_timestamp=now
        )

        record = MandateFailureRecord(
            record_id=f"rec_rzp_{payment_id}",
            mandate_id=mandate_id,
            merchant_id="mer_nbfc_001",
            customer_id=customer_id,
            customer_name=customer_name,
            amount_paise=amount_paise,
            decline_reason=decline_reason,
            decline_timestamp=now.isoformat(),
            prior_retry_count=0,
            prior_notifications=[],
            mandate_start_date=(now - datetime.resolution).isoformat(),
            mandate_end_date=None,
            source="razorpay_sandbox",
            ground_truth_reason=decline_reason,
            ground_truth_action=ground_truth_action
        )

        insert_failure_record(record.model_dump(), dataset_split="working")

        return {
            "status": "processed",
            "event": event_type,
            "record_id": record.record_id,
            "decline_reason": decline_reason.value,
            "ground_truth_action": ground_truth_action.value
        }

    return {"status": "ignored", "event": event_type}
