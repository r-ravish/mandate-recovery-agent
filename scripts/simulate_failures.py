"""
Script to simulate payment and mandate failure events from Razorpay Sandbox.
Usage:
    python scripts/simulate_failures.py [--mode local|api]

Modes:
- local: Simulates realistic webhook payloads directly into the local database / API endpoint.
- api: Connects to Razorpay Test Mode using keys in .env to create orders / test payments.
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
from src.schema import DeclineReason, CorrectAction, MandateFailureRecord
from src.database import insert_failure_record, get_failure_records
from src.data.synthetic_generator import determine_ground_truth_action


def simulate_local_webhook_failure(decline_type: str = "INSUFFICIENT_BALANCE"):
    """
    Simulate a realistic Razorpay webhook event directly into the local database,
    tagged as source='razorpay_sandbox'.
    """
    print(f"Simulating Razorpay sandbox failure for: {decline_type}...")
    now = datetime.now()
    payment_id = f"pay_test_{int(now.timestamp())}"
    mandate_id = f"sub_auto_sandbox_{payment_id[-4:]}"
    reason = DeclineReason(decline_type)
    amount_paise = 500000  # ₹5,000

    ground_truth_action = determine_ground_truth_action(
        reason=reason,
        prior_retries=0,
        prior_notifications=[],
        amount_paise=amount_paise,
        decline_timestamp=now
    )

    record = MandateFailureRecord(
        record_id=f"rec_sandbox_{payment_id[-6:]}",
        mandate_id=mandate_id,
        merchant_id="mer_nbfc_001",
        customer_id=f"cust_sb_{payment_id[-4:]}",
        customer_name="Rohan Sandbox",
        amount_paise=amount_paise,
        decline_reason=reason,
        decline_timestamp=now.isoformat(),
        prior_retry_count=0,
        prior_notifications=[],
        mandate_start_date=now.isoformat(),
        mandate_end_date=None,
        source="razorpay_sandbox",
        ground_truth_reason=reason,
        ground_truth_action=ground_truth_action
    )

    insert_failure_record(record.model_dump(), dataset_split="working")
    print(f"✓ Ingested sandbox record: {record.record_id} (Mandate: {record.mandate_id}, Reason: {record.decline_reason.value})")


def simulate_via_razorpay_api():
    """
    Connects to Razorpay Test API using credentials in .env.
    """
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        print("Warning: RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET not set in .env.")
        print("Using local sandbox simulation instead.")
        simulate_local_webhook_failure("INSUFFICIENT_BALANCE")
        simulate_local_webhook_failure("EXECUTION_WINDOW_BLOCKED")
        return

    import razorpay
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    print(f"Connected to Razorpay Test Mode with Key: {RAZORPAY_KEY_ID[:8]}...")
    try:
        # Create a test customer
        cust_res = client.customer.create({
            "name": "Priya Test User",
            "email": "priya.test@example.com",
            "contact": "9876543210"
        })
        print(f"✓ Created test customer: {cust_res.get('id')}")

        # Ingest a simulated failure corresponding to this test customer
        simulate_local_webhook_failure("INSUFFICIENT_BALANCE")
    except Exception as e:
        print(f"Razorpay API interaction notice: {e}")
        simulate_local_webhook_failure("INSUFFICIENT_BALANCE")


def main():
    parser = argparse.ArgumentParser(description="Simulate Razorpay mandate failures")
    parser.add_argument("--mode", choices=["local", "api"], default="local", help="Simulation mode")
    parser.add_argument("--reason", default="INSUFFICIENT_BALANCE", choices=[r.value for r in DeclineReason])
    args = parser.parse_args()

    if args.mode == "api":
        simulate_via_razorpay_api()
    else:
        simulate_local_webhook_failure(args.reason)


if __name__ == "__main__":
    main()
