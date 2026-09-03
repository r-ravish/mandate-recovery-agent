"""
FastAPI application for Mandate Recovery Agent.
Provides:
- Webhook ingestion from Razorpay Sandbox (/webhook/razorpay)
- Health check (/health)
- Endpoints to inspect failure records and audit logs (/api/records, /api/logs)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.webhook.razorpay_handler import router as webhook_router
from src.database import get_failure_records, get_audit_logs_for_record, init_database

# Initialize database on startup
init_database()

app = FastAPI(
    title="Mandate Recovery Agent API",
    version="1.0.0",
    description="Backend service for mandate failure ingestion, webhook handling, and audit inspection"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Webhook Router
app.include_router(webhook_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mandate-recovery-agent"}


@app.get("/api/records", tags=["records"])
async def list_records(split: str = "working"):
    """List mandate failure records by split ('working' or 'held_out')."""
    records = get_failure_records(dataset_split=split)
    return {"count": len(records), "split": split, "records": records}


@app.get("/api/records/{record_id}/audit", tags=["audit"])
async def get_record_audit_log(record_id: str):
    """Retrieve full audit trail for a specific failure record."""
    logs = get_audit_logs_for_record(record_id)
    return {"record_id": record_id, "count": len(logs), "logs": logs}
