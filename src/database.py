"""
SQLite database layer for Mandate Recovery Agent.
Manages schema initialization and CRUD operations for:
- mandate_failures: records of failed mandates (working + held-out splits)
- audit_log: immutable trace of every tool invocation and compliance check
- agent_decisions: final resolution decisions for each failure
- notification_log: history of customer notifications (PDN, nudges)
"""
import sqlite3
import json
from pathlib import Path
from typing import Optional, Any
from src.config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating parent directory if necessary."""
    db_path = Path(DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Dict-like access to columns
    return conn


def init_database():
    """Create all necessary tables and indexes if they do not exist."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Mandate Failure Records
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mandate_failures (
            record_id TEXT PRIMARY KEY,
            mandate_id TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            amount_paise INTEGER NOT NULL,
            decline_reason TEXT NOT NULL,
            decline_timestamp TEXT NOT NULL,
            prior_retry_count INTEGER DEFAULT 0,
            prior_notifications TEXT DEFAULT '[]',
            mandate_start_date TEXT NOT NULL,
            mandate_end_date TEXT,
            source TEXT NOT NULL,
            ground_truth_reason TEXT NOT NULL,
            ground_truth_action TEXT NOT NULL,
            dataset_split TEXT DEFAULT 'working'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mandate_failures_split ON mandate_failures(dataset_split)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_mandate_failures_mandate ON mandate_failures(mandate_id)")

    # 2. Audit Trail (Immutable log of all tool calls and compliance checks)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_arguments TEXT NOT NULL,
            agent_reasoning TEXT NOT NULL,
            result TEXT NOT NULL,
            compliance_check_passed INTEGER NOT NULL,
            compliance_violations TEXT DEFAULT '[]'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_record ON audit_log(record_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_mandate ON audit_log(mandate_id)")

    # 3. Agent Final Decisions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_decisions (
            record_id TEXT PRIMARY KEY,
            mandate_id TEXT NOT NULL,
            diagnosed_reason TEXT NOT NULL,
            chosen_action TEXT NOT NULL,
            action_arguments TEXT DEFAULT '{}',
            reasoning TEXT NOT NULL,
            steps_taken INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            compliance_violations TEXT DEFAULT '[]',
            success INTEGER DEFAULT 1
        )
    """)

    # 4. Notification Log (PDNs and nudges)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT NOT NULL,
            mandate_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            message TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            channel TEXT DEFAULT 'mock_sms'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notification_log_mandate ON notification_log(mandate_id)")

    conn.commit()
    conn.close()


def insert_failure_record(record: dict[str, Any], dataset_split: str = "working"):
    """Insert or update a mandate failure record."""
    conn = get_connection()
    rec = dict(record)
    rec["dataset_split"] = dataset_split
    if isinstance(rec.get("prior_notifications"), list):
        rec["prior_notifications"] = json.dumps(rec["prior_notifications"])

    columns = ", ".join(rec.keys())
    placeholders = ", ".join(["?"] * len(rec))
    sql = f"INSERT OR REPLACE INTO mandate_failures ({columns}) VALUES ({placeholders})"
    conn.execute(sql, list(rec.values()))
    conn.commit()
    conn.close()


def get_failure_record_by_id(record_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a single failure record by record_id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM mandate_failures WHERE record_id = ?", (record_id,)).fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    if isinstance(res.get("prior_notifications"), str):
        try:
            res["prior_notifications"] = json.loads(res["prior_notifications"])
        except json.JSONDecodeError:
            res["prior_notifications"] = []
    return res


def get_failure_records(dataset_split: Optional[str] = None) -> list[dict[str, Any]]:
    """Retrieve failure records, optionally filtered by dataset split."""
    conn = get_connection()
    if dataset_split:
        rows = conn.execute("SELECT * FROM mandate_failures WHERE dataset_split = ?", (dataset_split,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mandate_failures").fetchall()
    conn.close()

    results = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("prior_notifications"), str):
            try:
                d["prior_notifications"] = json.loads(d["prior_notifications"])
            except json.JSONDecodeError:
                d["prior_notifications"] = []
        results.append(d)
    return results


def log_audit_entry(
    record_id: str,
    mandate_id: str,
    timestamp: str,
    tool_name: str,
    tool_arguments: dict[str, Any],
    agent_reasoning: str,
    result: dict[str, Any],
    compliance_check_passed: bool,
    compliance_violations: Optional[list[str]] = None
):
    """Log an audit trail entry for a tool call."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO audit_log 
        (record_id, mandate_id, timestamp, tool_name, tool_arguments, agent_reasoning,
         result, compliance_check_passed, compliance_violations)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record_id,
        mandate_id,
        timestamp,
        tool_name,
        json.dumps(tool_arguments),
        agent_reasoning,
        json.dumps(result),
        1 if compliance_check_passed else 0,
        json.dumps(compliance_violations or [])
    ))
    conn.commit()
    conn.close()


def log_notification(
    record_id: str,
    mandate_id: str,
    customer_id: str,
    notification_type: str,
    message: str,
    sent_at: str,
    channel: str = "mock_sms"
) -> int:
    """Log a notification sent to a customer. Returns generated notification_id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO notification_log 
        (record_id, mandate_id, customer_id, notification_type, message, sent_at, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (record_id, mandate_id, customer_id, notification_type, message, sent_at, channel))
    notif_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return notif_id


def get_notifications_for_mandate(mandate_id: str) -> list[dict[str, Any]]:
    """Retrieve all notifications logged for a mandate."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM notification_log WHERE mandate_id = ? ORDER BY sent_at ASC",
        (mandate_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_audit_logs_for_record(record_id: str) -> list[dict[str, Any]]:
    """Retrieve full audit log for a specific failure record."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE record_id = ? ORDER BY log_id ASC",
        (record_id,)
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["tool_arguments"] = json.loads(d["tool_arguments"])
        d["result"] = json.loads(d["result"])
        d["compliance_violations"] = json.loads(d["compliance_violations"])
        results.append(d)
    return results


def get_agent_decision(record_id: str) -> Optional[dict[str, Any]]:
    """Retrieve agent decision for a given record_id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM agent_decisions WHERE record_id = ?", (record_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["action_arguments"] = json.loads(d["action_arguments"]) if d["action_arguments"] else {}
    d["compliance_violations"] = json.loads(d["compliance_violations"]) if d["compliance_violations"] else []
    return d


def get_all_agent_decisions() -> list[dict[str, Any]]:
    """Retrieve all agent decisions ordered by latest timestamp."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM agent_decisions ORDER BY timestamp DESC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["action_arguments"] = json.loads(d["action_arguments"]) if d["action_arguments"] else {}
        d["compliance_violations"] = json.loads(d["compliance_violations"]) if d["compliance_violations"] else []
        results.append(d)
    return results


def get_all_audit_logs(limit: int = 300) -> list[dict[str, Any]]:
    """Retrieve recent audit logs across all records."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["tool_arguments"] = json.loads(d["tool_arguments"])
        except Exception:
            pass
        try:
            d["result"] = json.loads(d["result"])
        except Exception:
            pass
        try:
            d["compliance_violations"] = json.loads(d["compliance_violations"])
        except Exception:
            pass
        results.append(d)
    return results


def get_dashboard_kpis() -> dict[str, Any]:
    """Calculate high-level KPI metrics for the executive dashboard."""
    conn = get_connection()
    total_failures = conn.execute("SELECT COUNT(*) FROM mandate_failures").fetchone()[0]
    total_at_risk_paise = conn.execute("SELECT COALESCE(SUM(amount_paise), 0) FROM mandate_failures").fetchone()[0]

    # Decisions metrics
    total_decisions = conn.execute("SELECT COUNT(*) FROM agent_decisions").fetchone()[0]
    escalations = conn.execute("SELECT COUNT(*) FROM agent_decisions WHERE chosen_action = 'escalate_to_human'").fetchone()[0]
    
    # Recovered amount = amount of records where chosen_action was schedule_retry or log_promise_to_pay with success = 1
    recovered_paise = conn.execute("""
        SELECT COALESCE(SUM(f.amount_paise), 0)
        FROM mandate_failures f
        JOIN agent_decisions d ON f.record_id = d.record_id
        WHERE d.chosen_action IN ('schedule_retry', 'log_promise_to_pay')
          AND d.success = 1
    """).fetchone()[0]

    # Audit compliance stats
    audit_total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    audit_passed = conn.execute("SELECT COUNT(*) FROM audit_log WHERE compliance_check_passed = 1").fetchone()[0]
    audit_blocked = conn.execute("SELECT COUNT(*) FROM audit_log WHERE compliance_check_passed = 0").fetchone()[0]
    conn.close()

    adherence_pct = (audit_passed / audit_total * 100) if audit_total > 0 else 100.0
    recovery_rate_pct = (recovered_paise / total_at_risk_paise * 100) if total_at_risk_paise > 0 else 0.0

    return {
        "total_failures": total_failures,
        "total_at_risk_inr": total_at_risk_paise / 100.0,
        "total_recovered_inr": recovered_paise / 100.0,
        "recovery_rate_pct": recovery_rate_pct,
        "total_decisions": total_decisions,
        "escalations": escalations,
        "audit_total": audit_total,
        "audit_passed": audit_passed,
        "audit_blocked": audit_blocked,
        "adherence_pct": adherence_pct,
    }

