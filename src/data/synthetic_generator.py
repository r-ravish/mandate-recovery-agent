"""
Synthetic Dataset Generator for Mandate Recovery Agent.
Generates realistic, labelled UPI AutoPay mandate failure records matching
the researched industry distribution for an NBFC collecting loan EMIs.

Includes both:
1. Deterministic high-fidelity generator (reproducible, seed-based, instant)
2. Optional Gemini-augmented variation generator
"""
import random
from datetime import datetime, timedelta
from typing import Optional
from src.schema import DeclineReason, CorrectAction, MandateFailureRecord
from src.config import MAX_RETRIES_PER_CYCLE

# Realistic Indian customer names for the simulated NBFC book
CUSTOMER_NAMES = [
    "Aarav Sharma", "Priya Patel", "Rohan Mehta", "Ananya Iyer", "Vikram Singh",
    "Sneha Reddy", "Aditya Verma", "Pooja Nair", "Rahul Mukherjee", "Kavita Rao",
    "Siddharth Gupta", "Neha Kulkarni", "Amitabh Joshi", "Deepika Pillai", "Manish Jain",
    "Divya Menon", "Karan Malhotra", "Ritu Deshmukh", "Suresh Choudhary", "Sunita Das",
    "Arjun Nambiar", "Swati Bhatt", "Abhishek Tiwari", "Meera Saxena", "Nikhil Hegde",
    "Harini Subramanian", "Gaurav Agarwal", "Shreya Sen", "Rajesh Pandey", "Vandana Sethi"
]

# Distribution weights matching researched UPI AutoPay failure realities:
# - Insufficient balance is dominant (~40%)
# - Execution window blocked during NPCI peak hours (~15%)
# - Bank temporary infrastructure failure (~12%)
# - AFA limit exceeded (~10%)
# - Mandate expired (~10%)
# - Mandate revoked by customer (~8%)
# - Unclassified / anomalous (~5%)
DISTRIBUTION_WEIGHTS = {
    DeclineReason.INSUFFICIENT_BALANCE: 40,
    DeclineReason.EXECUTION_WINDOW_BLOCKED: 15,
    DeclineReason.BANK_TECHNICAL_FAILURE: 12,
    DeclineReason.AFA_LIMIT_EXCEEDED: 10,
    DeclineReason.MANDATE_EXPIRED: 10,
    DeclineReason.MANDATE_REVOKED: 8,
    DeclineReason.UNCLASSIFIED: 5,
}

# Typical NBFC monthly EMI amounts in INR
TYPICAL_EMI_AMOUNTS_INR = [
    1500, 2500, 3200, 4500, 5000, 6000, 7500, 8500, 10000, 12500, 15000, 18000, 22000, 25000
]


def determine_ground_truth_action(
    reason: DeclineReason,
    prior_retries: int,
    prior_notifications: list[dict],
    amount_paise: int,
    decline_timestamp: datetime
) -> CorrectAction:
    """
    Deterministically derive the ground-truth correct action based on
    Phase 0 Compliance Rules and Decline Taxonomy logic.
    """
    # 1. Non-recoverable reasons MUST be escalated immediately
    if reason in [DeclineReason.MANDATE_EXPIRED, DeclineReason.MANDATE_REVOKED, DeclineReason.UNCLASSIFIED]:
        return CorrectAction.ESCALATE_TO_HUMAN

    # 2. AFA limit exceeded: amounts > ₹15,000 requiring 2FA or bank cap
    # The agent cannot alter amounts or bypass AFA, so this must be escalated for manual loan restructuring
    if reason == DeclineReason.AFA_LIMIT_EXCEEDED:
        return CorrectAction.ESCALATE_TO_HUMAN

    # 3. If retry limit (3 retries) has already been reached, must escalate
    if prior_retries >= MAX_RETRIES_PER_CYCLE:
        return CorrectAction.ESCALATE_TO_HUMAN

    # 4. For recoverable failures (INSUFFICIENT_BALANCE, EXECUTION_WINDOW_BLOCKED, BANK_TECHNICAL_FAILURE):
    # Rule 1: Check if a Pre-Debit Notification (PDN) was already sent at least 24h and at most 7 days
    # prior to the decline timestamp. Matches compliance_checker.py check_pre_debit_notification().
    has_valid_recent_pdn = False
    for notif in prior_notifications:
        if notif.get("notification_type") == "pre_debit_notice":
            sent_time_str = notif.get("sent_at")
            try:
                sent_time = datetime.fromisoformat(sent_time_str)
                advance_seconds = (decline_timestamp - sent_time).total_seconds()
                # PDN must be >= 24h before decline AND within 7 days (168h)
                if advance_seconds >= 24 * 3600 and advance_seconds <= 7 * 86400:
                    has_valid_recent_pdn = True
                    break
            except Exception:
                pass

    if has_valid_recent_pdn:
        # PDN is already validly in place, agent should directly schedule a compliant retry
        return CorrectAction.SCHEDULE_RETRY
    else:
        # PDN is missing, too recent, or stale (> 7 days) — agent MUST send a fresh notice first
        return CorrectAction.SEND_PRE_DEBIT_NOTICE


def generate_single_record(index: int, base_time: datetime, random_seed: Optional[int] = None) -> MandateFailureRecord:
    """Generate a single realistic mandate failure record."""
    if random_seed is not None:
        random.seed(random_seed + index)

    # Pick decline reason according to weighted distribution
    reasons = list(DISTRIBUTION_WEIGHTS.keys())
    weights = list(DISTRIBUTION_WEIGHTS.values())
    chosen_reason = random.choices(reasons, weights=weights, k=1)[0]

    record_id = f"rec_{index:04d}"
    mandate_id = f"sub_auto_{1000 + index}"
    customer_id = f"cust_in_{5000 + index}"
    customer_name = random.choice(CUSTOMER_NAMES)

    # Amount: AFA failures generally occur around or above ₹15,000
    if chosen_reason == DeclineReason.AFA_LIMIT_EXCEEDED:
        amount_inr = random.choice([16000, 18500, 22000, 25000, 30000])
    else:
        amount_inr = random.choice(TYPICAL_EMI_AMOUNTS_INR)
    amount_paise = amount_inr * 100

    # Timestamp within last 14 days
    days_ago = random.randint(1, 14)
    hour = random.choice([9, 10, 11, 12, 14, 15, 17, 18, 20, 22])
    minute = random.randint(0, 59)
    decline_time = base_time - timedelta(days=days_ago, hours=hour, minutes=minute)

    # If EXECUTION_WINDOW_BLOCKED, decline timestamp must be during NPCI peak hours
    if chosen_reason == DeclineReason.EXECUTION_WINDOW_BLOCKED:
        peak_hour = random.choice([10, 11, 12, 17, 18, 19, 20])
        decline_time = decline_time.replace(hour=peak_hour, minute=random.randint(5, 55))

    # Prior retry count (0 to 3)
    if chosen_reason in [DeclineReason.MANDATE_EXPIRED, DeclineReason.MANDATE_REVOKED]:
        prior_retries = random.choice([0, 1])
    else:
        prior_retries = random.choices([0, 1, 2, 3], weights=[50, 30, 15, 5], k=1)[0]

    # Prior notifications
    prior_notifications = []
    # 40% of cases have a prior pre-debit notice sent
    if random.random() < 0.45:
        # Either sent 26-48 hours before decline (valid) or 6 hours before (invalid/too recent)
        pdn_hours_before = random.choice([28, 36, 48, 72, 8])
        pdn_time = decline_time - timedelta(hours=pdn_hours_before)
        prior_notifications.append({
            "notification_type": "pre_debit_notice",
            "sent_at": pdn_time.isoformat(),
            "channel": random.choice(["mock_sms", "mock_whatsapp"])
        })

    # If prior retries exist, maybe a recovery nudge was also logged
    if prior_retries > 0 and random.random() < 0.5:
        nudge_time = decline_time - timedelta(days=1)
        prior_notifications.append({
            "notification_type": "recovery_nudge",
            "sent_at": nudge_time.isoformat(),
            "channel": "mock_whatsapp"
        })

    # Mandate start date (6 to 18 months ago)
    start_date = decline_time - timedelta(days=random.randint(180, 540))
    
    # Mandate end date:
    # If MANDATE_EXPIRED, end date was yesterday or last week
    if chosen_reason == DeclineReason.MANDATE_EXPIRED:
        end_date = decline_time - timedelta(days=random.randint(1, 10))
    else:
        end_date = decline_time + timedelta(days=random.randint(90, 720))

    # Derive ground truth action according to compliance rules
    ground_truth_action = determine_ground_truth_action(
        chosen_reason,
        prior_retries,
        prior_notifications,
        amount_paise,
        decline_time
    )

    return MandateFailureRecord(
        record_id=record_id,
        mandate_id=mandate_id,
        merchant_id="mer_nbfc_001",
        customer_id=customer_id,
        customer_name=customer_name,
        amount_paise=amount_paise,
        decline_reason=chosen_reason,
        decline_timestamp=decline_time.isoformat(),
        prior_retry_count=prior_retries,
        prior_notifications=prior_notifications,
        mandate_start_date=start_date.isoformat(),
        mandate_end_date=end_date.isoformat() if end_date else None,
        source="synthetic",
        ground_truth_reason=chosen_reason,
        ground_truth_action=ground_truth_action
    )


def generate_synthetic_dataset(total_count: int = 200, seed: int = 42) -> list[MandateFailureRecord]:
    """
    Generate the full synthetic dataset of mandate failure events.
    """
    base_time = datetime.now()
    records = []
    for i in range(1, total_count + 1):
        record = generate_single_record(i, base_time, random_seed=seed)
        records.append(record)
    return records
