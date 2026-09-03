"""
Central configuration for Mandate Recovery Agent.
Loads environment variables and defines compliance & system constants.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file from root
load_dotenv(dotenv_path=BASE_DIR / ".env")

# --- API Keys & Credentials ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# --- Database & Storage Paths ---
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = str(DATA_DIR / "mandate_failures.db")
WORKING_SET_PATH = str(DATA_DIR / "working_set.json")
HELD_OUT_SET_PATH = str(DATA_DIR / "held_out_set.json")

# --- Compliance Constants ---
MAX_RETRIES_PER_CYCLE = 3
PRE_DEBIT_NOTICE_HOURS = 24

# NPCI UPI AutoPay 2026 Restricted Peak Windows
# Debits attempted during these time windows will be rejected by NPCI
BLOCKED_WINDOWS = [
    ("10:00", "13:00"),  # 10:00 AM - 1:00 PM (Peak morning congestion)
    ("17:00", "21:30"),  # 5:00 PM - 9:30 PM (Peak evening congestion)
]

# --- Decline Reason Taxonomy (Locked 7 Reasons) ---
DECLINE_REASONS = [
    "INSUFFICIENT_BALANCE",
    "MANDATE_EXPIRED",
    "EXECUTION_WINDOW_BLOCKED",
    "AFA_LIMIT_EXCEEDED",
    "MANDATE_REVOKED",
    "BANK_TECHNICAL_FAILURE",
    "UNCLASSIFIED",
]

# Decline reasons that CANNOT be recovered automatically -> Escalate immediately
NON_RECOVERABLE_REASONS = [
    "MANDATE_EXPIRED",
    "MANDATE_REVOKED",
    "UNCLASSIFIED",
]

# Actions allowed in the system
ALLOWED_ACTIONS = [
    "schedule_retry",
    "send_pre_debit_notice",
    "send_recovery_nudge",
    "log_promise_to_pay",
    "escalate_to_human",
]
