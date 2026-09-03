"""
Pydantic data models for Mandate Recovery Agent.
Defines schemas for decline events, notifications, actions, and audit logs.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field


class DeclineReason(str, Enum):
    """The 7 fixed decline reasons recognized by the agent."""
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    EXECUTION_WINDOW_BLOCKED = "EXECUTION_WINDOW_BLOCKED"
    AFA_LIMIT_EXCEEDED = "AFA_LIMIT_EXCEEDED"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    BANK_TECHNICAL_FAILURE = "BANK_TECHNICAL_FAILURE"
    UNCLASSIFIED = "UNCLASSIFIED"


class CorrectAction(str, Enum):
    """The 5 allowed actions the agent can take."""
    SCHEDULE_RETRY = "schedule_retry"
    SEND_PRE_DEBIT_NOTICE = "send_pre_debit_notice"
    SEND_RECOVERY_NUDGE = "send_recovery_nudge"
    LOG_PROMISE_TO_PAY = "log_promise_to_pay"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class NotificationEntry(BaseModel):
    """Historical notification logged for a customer/mandate."""
    notification_id: Optional[int] = None
    notification_type: str = Field(description="pre_debit_notice, recovery_nudge, etc.")
    sent_at: str = Field(description="ISO format timestamp")
    channel: str = Field(default="mock_sms", description="mock_sms, mock_whatsapp, mock_email")
    message: Optional[str] = None


class MandateFailureRecord(BaseModel):
    """
    Schema for a single failed mandate execution event.
    Represents an event originating from Razorpay webhook or synthetic generation.
    """
    record_id: str = Field(description="Unique record identifier, e.g., rec_001")
    mandate_id: str = Field(description="Razorpay mandate/subscription ID, e.g., sub_123 or man_123")
    merchant_id: str = Field(default="mer_nbfc_001", description="Merchant ID")
    customer_id: str = Field(description="Customer identifier, e.g., cust_456")
    customer_name: str = Field(description="Customer display name")
    amount_paise: int = Field(description="Amount in paise (e.g. ₹5,000 = 500000 paise)", gt=0)
    decline_reason: DeclineReason = Field(description="Diagnosed or incoming decline reason")
    decline_timestamp: str = Field(description="ISO format timestamp when the failure occurred")
    prior_retry_count: int = Field(default=0, ge=0, description="Number of retries already executed")
    prior_notifications: list[dict] = Field(
        default_factory=list,
        description="List of prior notification dicts: [{notification_type, sent_at, channel}]"
    )
    mandate_start_date: str = Field(description="ISO format start date of the mandate")
    mandate_end_date: Optional[str] = Field(default=None, description="ISO format expiry date")
    source: Literal["razorpay_sandbox", "synthetic"] = Field(description="Data origin")

    # --- Ground Truth (Evaluation Only, NEVER shown to agent during reasoning) ---
    ground_truth_reason: DeclineReason
    ground_truth_action: CorrectAction


class AgentDecision(BaseModel):
    """Structured decision output produced by the agent."""
    record_id: str
    mandate_id: str
    diagnosed_reason: DeclineReason
    chosen_action: CorrectAction
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(description="Agent's natural language chain of thought / rationale")
    steps_taken: int = Field(default=1, ge=1)
    timestamp: str = Field(description="ISO timestamp of decision")
    compliance_violations: list[str] = Field(default_factory=list)
    success: bool = Field(default=True)
