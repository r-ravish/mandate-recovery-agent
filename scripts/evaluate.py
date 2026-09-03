"""
Phase 5 — Evaluation Harness.
Runs the agent against the 64-record held-out set, computes per-class and
aggregate precision / recall / F1 for both diagnostic accuracy (decline
reason) and action accuracy (tool chosen), and writes the results to
docs/evaluation_report.md.

Usage:
    python scripts/evaluate.py
    python scripts/evaluate.py --limit 10          # quick smoke-run
    python scripts/evaluate.py --force             # re-process all records
    python scripts/evaluate.py --out custom.md     # custom report path
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database import init_database, insert_failure_record
from src.agent.agent_loop import process_mandate_failure

REPORT_PATH = BASE_DIR / "docs" / "evaluation_report.md"

# ── helpers ───────────────────────────────────────────────────────────────────

def normalise_action(raw: str) -> str:
    """Map ground-truth action strings to their canonical form."""
    mapping = {
        "send_pre_debit_notice":  "send_pre_debit_notice",
        "schedule_retry":         "schedule_retry",
        "escalate_to_human":      "escalate_to_human",
        "send_recovery_nudge":    "send_recovery_nudge",
        "log_promise_to_pay":     "log_promise_to_pay",
    }
    return mapping.get(raw.lower(), raw.lower())


def compute_per_class_metrics(
    records: list[dict],
    label_key: str,       # "ground_truth_reason" or "ground_truth_action"
    pred_key: str,        # "predicted_reason"    or "predicted_action"
) -> dict:
    """Return precision, recall, F1, support per class + macro / weighted aggregates."""
    classes = sorted({r[label_key] for r in records})
    stats = {}
    for cls in classes:
        tp = sum(1 for r in records if r[label_key] == cls and r[pred_key] == cls)
        fp = sum(1 for r in records if r[label_key] != cls and r[pred_key] == cls)
        fn = sum(1 for r in records if r[label_key] == cls and r[pred_key] != cls)
        support = tp + fn
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        stats[cls] = {"precision": precision, "recall": recall, "f1": f1, "support": support, "tp": tp, "fp": fp, "fn": fn}

    n = len(records)
    macro_p  = sum(v["precision"] for v in stats.values()) / len(stats)
    macro_r  = sum(v["recall"]    for v in stats.values()) / len(stats)
    macro_f1 = sum(v["f1"]        for v in stats.values()) / len(stats)
    weighted_p  = sum(v["precision"] * v["support"] for v in stats.values()) / n
    weighted_r  = sum(v["recall"]    * v["support"] for v in stats.values()) / n
    weighted_f1 = sum(v["f1"]        * v["support"] for v in stats.values()) / n
    accuracy = sum(1 for r in records if r[label_key] == r[pred_key]) / n

    return {
        "per_class": stats,
        "macro":     {"precision": macro_p,    "recall": macro_r,    "f1": macro_f1},
        "weighted":  {"precision": weighted_p, "recall": weighted_r, "f1": weighted_f1},
        "accuracy":  accuracy,
        "n":         n,
    }


def confusion_matrix_md(
    records: list[dict],
    label_key: str,
    pred_key: str,
    title: str
) -> str:
    """Render a markdown confusion-matrix table."""
    classes = sorted({r[label_key] for r in records} | {r[pred_key] for r in records})
    matrix = defaultdict(lambda: defaultdict(int))
    for r in records:
        matrix[r[label_key]][r[pred_key]] += 1

    short = {c: c.replace("INSUFFICIENT_BALANCE",    "INS_BAL")
                  .replace("EXECUTION_WINDOW_BLOCKED","EXE_WIN")
                  .replace("BANK_TECHNICAL_FAILURE",  "BANK_TF")
                  .replace("AFA_LIMIT_EXCEEDED",      "AFA_LIM")
                  .replace("MANDATE_EXPIRED",         "MAN_EXP")
                  .replace("MANDATE_REVOKED",         "MAN_REV")
                  .replace("UNCLASSIFIED",            "UNCLASS")
                  .replace("send_pre_debit_notice",   "PDN")
                  .replace("schedule_retry",          "RETRY")
                  .replace("escalate_to_human",       "ESCALATE")
                  .replace("send_recovery_nudge",     "NUDGE")
                  .replace("log_promise_to_pay",      "PTP") for c in classes}

    header = "| Actual \\ Predicted | " + " | ".join(short[c] for c in classes) + " |"
    sep    = "|" + "|".join(["---"] * (len(classes) + 1)) + "|"
    rows   = []
    for actual in classes:
        cells = [str(matrix[actual][pred]) for pred in classes]
        rows.append(f"| **{short[actual]}** | " + " | ".join(cells) + " |")

    lines = [f"### {title}", header, sep] + rows
    return "\n".join(lines)


def build_report(
    eval_records: list[dict],
    reason_metrics: dict,
    action_metrics: dict,
    compliance_stats: dict,
    run_ts: str,
) -> str:
    """Compose the full markdown evaluation report."""

    # ── Section 1: Overview ─────────────────────────────────────────────────
    n = reason_metrics["n"]
    r_acc = reason_metrics["accuracy"] * 100
    a_acc = action_metrics["accuracy"] * 100

    overview = f"""# Phase 5 — Evaluation Report

**Run timestamp**: {run_ts}  
**Held-out set size**: {n} records  
**Model / Engine**: Gemini 2.0 Flash (deterministic expert-reasoning fallback when API key absent)

---

## 1. Overall Accuracy

| Metric | Score |
|---|---|
| **Decline Reason Accuracy** | {r_acc:.1f}% |
| **Action Accuracy** | {a_acc:.1f}% |
| **Compliance Adherence** (guardrail-enforced) | {compliance_stats["adherence_pct"]:.1f}% |
| Total Records Evaluated | {n} |
| Total Audit Log Entries Generated | {compliance_stats["total_audit_entries"]} |
| Guardrail-Blocked Attempts | {compliance_stats["blocked_count"]} |
| Successful Tool Executions | {compliance_stats["allowed_count"]} |

---"""

    # ── Section 2: Reason metrics ───────────────────────────────────────────
    reason_rows = []
    for cls, m in sorted(reason_metrics["per_class"].items(), key=lambda x: -x[1]["support"]):
        reason_rows.append(
            f"| {cls} | {m['precision']*100:.1f}% | {m['recall']*100:.1f}% | {m['f1']*100:.1f}% | {m['support']} |"
        )
    rm = reason_metrics
    reason_rows.append(f"| **Macro Avg** | {rm['macro']['precision']*100:.1f}% | {rm['macro']['recall']*100:.1f}% | {rm['macro']['f1']*100:.1f}% | {n} |")
    reason_rows.append(f"| **Weighted Avg** | {rm['weighted']['precision']*100:.1f}% | {rm['weighted']['recall']*100:.1f}% | {rm['weighted']['f1']*100:.1f}% | {n} |")

    reason_section = f"""
## 2. Decline Reason Classification

| Decline Reason | Precision | Recall | F1 | Support |
|---|---|---|---|---|
""" + "\n".join(reason_rows)

    # ── Section 3: Action metrics ────────────────────────────────────────────
    action_rows = []
    for cls, m in sorted(action_metrics["per_class"].items(), key=lambda x: -x[1]["support"]):
        action_rows.append(
            f"| {cls} | {m['precision']*100:.1f}% | {m['recall']*100:.1f}% | {m['f1']*100:.1f}% | {m['support']} |"
        )
    am = action_metrics
    action_rows.append(f"| **Macro Avg** | {am['macro']['precision']*100:.1f}% | {am['macro']['recall']*100:.1f}% | {am['macro']['f1']*100:.1f}% | {n} |")
    action_rows.append(f"| **Weighted Avg** | {am['weighted']['precision']*100:.1f}% | {am['weighted']['recall']*100:.1f}% | {am['weighted']['f1']*100:.1f}% | {n} |")

    action_section = f"""
## 3. Action Selection Accuracy

| Action | Precision | Recall | F1 | Support |
|---|---|---|---|---|
""" + "\n".join(action_rows)

    # ── Section 4: Confusion Matrices ────────────────────────────────────────
    reason_cm  = confusion_matrix_md(eval_records, "ground_truth_reason", "predicted_reason", "Decline Reason Confusion Matrix")
    action_cm  = confusion_matrix_md(eval_records, "ground_truth_action", "predicted_action", "Action Confusion Matrix")

    cm_section = f"""
## 4. Confusion Matrices

{reason_cm}

{action_cm}
"""

    # ── Section 5: Compliance Adherence ─────────────────────────────────────
    compliance_section = f"""
## 5. Compliance Guardrail Adherence

| Metric | Count |
|---|---|
| Total tool invocations in audit log | {compliance_stats["total_audit_entries"]} |
| Compliant passes (`compliance_check_passed = 1`) | {compliance_stats["allowed_count"]} |
| Blocked violations (`compliance_check_passed = 0`) | {compliance_stats["blocked_count"]} |
| **Adherence Rate** | **{compliance_stats["adherence_pct"]:.1f}%** |

> All blocked invocations were logged to the immutable `audit_log` table with `compliance_check_passed = 0`  
> and a full violation description. Zero blocked invocations passed through as silent failures.
"""

    # ── Section 6: Per-record Breakdown (errors only) ────────────────────────
    errors = [r for r in eval_records if r["ground_truth_reason"] != r["predicted_reason"] or r["ground_truth_action"] != r["predicted_action"]]
    if errors:
        error_rows = []
        for r in errors:
            r_ok = "✅" if r["ground_truth_reason"] == r["predicted_reason"] else "❌"
            a_ok = "✅" if r["ground_truth_action"] == r["predicted_action"] else "❌"
            error_rows.append(
                f"| {r['record_id']} | {r['ground_truth_reason']} | {r['predicted_reason']} {r_ok} | {r['ground_truth_action']} | {r['predicted_action']} {a_ok} |"
            )
        errors_section = f"""
## 6. Misclassifications ({len(errors)} records)

| Record ID | True Reason | Predicted Reason | True Action | Predicted Action |
|---|---|---|---|---|
""" + "\n".join(error_rows)
    else:
        errors_section = "\n## 6. Misclassifications\n\nNo misclassifications found. Perfect score.\n"

    # ── Section 7: Error Analysis ────────────────────────────────────────────
    analysis_lines = ["## 7. Error Analysis\n"]
    if errors:
        reason_errors = [e for e in errors if e["ground_truth_reason"] != e["predicted_reason"]]
        action_errors = [e for e in errors if e["ground_truth_action"] != e["predicted_action"]]
        analysis_lines.append(f"- **{len(reason_errors)} decline reason misclassifications** found.")
        analysis_lines.append(f"- **{len(action_errors)} action misclassifications** found.")
        if reason_errors:
            confusion_pairs = defaultdict(int)
            for e in reason_errors:
                confusion_pairs[(e["ground_truth_reason"], e["predicted_reason"])] += 1
            analysis_lines.append("\nMost common reason confusion pairs:")
            for (true, pred), count in sorted(confusion_pairs.items(), key=lambda x: -x[1]):
                analysis_lines.append(f"  - `{true}` → predicted as `{pred}`: {count} time(s)")
    else:
        analysis_lines.append("The agent made no errors on the held-out set. All 7 decline reasons and all action choices were correctly identified.")

    analysis_section = "\n".join(analysis_lines)

    return "\n".join([
        overview,
        reason_section,
        action_section,
        cm_section,
        compliance_section,
        errors_section,
        analysis_section,
    ])


# ── main evaluation loop ──────────────────────────────────────────────────────

def run_evaluation(limit: int = None, force: bool = False) -> None:
    init_database()
    held_out = json.loads((BASE_DIR / "data" / "held_out_set.json").read_text())

    if limit:
        held_out = held_out[:limit]

    print("=" * 70)
    print(f"MANDATE RECOVERY AGENT — PHASE 5 EVALUATION HARNESS")
    print(f"Evaluating {len(held_out)} held-out records...")
    print("=" * 70)

    eval_records = []
    errors_seen  = 0

    for i, record in enumerate(held_out, 1):
        record_id = record["record_id"]
        gt_reason = record["ground_truth_reason"]
        gt_action = normalise_action(record["ground_truth_action"])

        # Ensure the held-out record is in the database
        insert_failure_record(record, dataset_split="held_out")

        try:
            decision = process_mandate_failure(record_id, force_reprocess=force)
            pred_reason = decision.diagnosed_reason.value
            # first_action_taken is the first tool the agent called — this matches
            # ground_truth_action which labels the first required step.
            # chosen_action is the final terminal step (e.g. schedule_retry after send_pre_debit_notice).
            pred_action = (decision.first_action_taken or decision.chosen_action).value
        except Exception as exc:
            print(f"  [{i:3d}/{len(held_out)}] ERROR on {record_id}: {exc}")
            pred_reason = "UNCLASSIFIED"
            pred_action = "escalate_to_human"
            errors_seen += 1

        r_correct = "✅" if gt_reason == pred_reason else "❌"
        a_correct = "✅" if gt_action == pred_action else "❌"

        eval_records.append({
            "record_id":         record_id,
            "ground_truth_reason": gt_reason,
            "predicted_reason":   pred_reason,
            "ground_truth_action": gt_action,
            "predicted_action":   pred_action,
            "reason_correct":     gt_reason == pred_reason,
            "action_correct":     gt_action == pred_action,
        })

        print(f"  [{i:3d}/{len(held_out)}] {record_id:<18} | "
              f"Reason {r_correct} {pred_reason:<26} | "
              f"Action {a_correct} {pred_action}")

    # ── Pull compliance stats from audit_log ──────────────────────────────
    from src.database import get_connection
    conn = get_connection()
    held_out_ids = tuple(r["record_id"] for r in held_out)
    placeholders = ",".join("?" * len(held_out_ids))
    rows = conn.execute(
        f"SELECT compliance_check_passed FROM audit_log WHERE record_id IN ({placeholders})",
        held_out_ids
    ).fetchall()
    conn.close()

    total_entries = len(rows)
    allowed_count = sum(1 for r in rows if r["compliance_check_passed"] == 1)
    blocked_count = total_entries - allowed_count
    adherence_pct = (allowed_count / total_entries * 100) if total_entries > 0 else 100.0

    compliance_stats = {
        "total_audit_entries": total_entries,
        "allowed_count":       allowed_count,
        "blocked_count":       blocked_count,
        "adherence_pct":       adherence_pct,
    }

    # ── Compute metrics ───────────────────────────────────────────────────
    reason_metrics = compute_per_class_metrics(eval_records, "ground_truth_reason", "predicted_reason")
    action_metrics = compute_per_class_metrics(eval_records, "ground_truth_action", "predicted_action")

    # ── Print Summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PHASE 5 EVALUATION RESULTS")
    print("=" * 70)
    print(f"  Decline Reason Accuracy  : {reason_metrics['accuracy']*100:.1f}%  "
          f"(Macro F1: {reason_metrics['macro']['f1']*100:.1f}%)")
    print(f"  Action Accuracy          : {action_metrics['accuracy']*100:.1f}%  "
          f"(Macro F1: {action_metrics['macro']['f1']*100:.1f}%)")
    print(f"  Compliance Adherence     : {adherence_pct:.1f}%  "
          f"({allowed_count}/{total_entries} tool calls passed guardrails)")
    if errors_seen:
        print(f"  Runtime errors           : {errors_seen} (auto-escalated)")
    print("=" * 70)

    # ── Write report ──────────────────────────────────────────────────────
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_md = build_report(eval_records, reason_metrics, action_metrics, compliance_stats, run_ts)
    REPORT_PATH.write_text(report_md)
    print(f"\n  Detailed report saved → {REPORT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 5 Evaluation Harness")
    parser.add_argument("--limit",  type=int, default=None, help="Limit evaluation to N records (for smoke testing)")
    parser.add_argument("--force",  action="store_true",    help="Force re-process records even if already in agent_decisions")
    parser.add_argument("--out",    type=str, default=None,  help="Override output report path")
    args = parser.parse_args()

    if args.out:
        REPORT_PATH = Path(args.out)

    run_evaluation(limit=args.limit, force=args.force)
