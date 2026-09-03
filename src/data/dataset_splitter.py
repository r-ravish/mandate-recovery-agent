"""
Dataset Splitter for Mandate Recovery Agent.
Splits the generated records into:
- 70% Working Set: for iterative development, prompt tuning, and basic testing
- 30% Held-Out Set: untouched until Phase 5 evaluation

Ensures consistent distribution of decline reasons across both sets.
"""
import json
import random
from pathlib import Path
from src.schema import MandateFailureRecord, DeclineReason
from src.config import WORKING_SET_PATH, HELD_OUT_SET_PATH
from src.database import insert_failure_record, init_database


def split_and_persist_dataset(
    records: list[MandateFailureRecord],
    split_ratio: float = 0.70,
    seed: int = 42
) -> tuple[list[dict], list[dict]]:
    """
    Split records into working and held-out sets, write JSON files,
    and persist into SQLite database.
    """
    # Group by decline reason for stratified splitting
    by_reason: dict[DeclineReason, list[MandateFailureRecord]] = {}
    for r in records:
        by_reason.setdefault(r.decline_reason, []).append(r)

    random.seed(seed)
    working_records: list[dict] = []
    held_out_records: list[dict] = []

    for reason, group in by_reason.items():
        shuffled = list(group)
        random.shuffle(shuffled)
        cut = int(len(shuffled) * split_ratio)
        working_part = shuffled[:cut]
        held_out_part = shuffled[cut:]

        working_records.extend([r.model_dump() for r in working_part])
        held_out_records.extend([r.model_dump() for r in held_out_part])

    # Re-shuffle both sets
    random.shuffle(working_records)
    random.shuffle(held_out_records)

    # Save to JSON artifacts
    Path(WORKING_SET_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(WORKING_SET_PATH, "w") as f:
        json.dump(working_records, f, indent=2)

    with open(HELD_OUT_SET_PATH, "w") as f:
        json.dump(held_out_records, f, indent=2)

    # Ensure SQLite tables exist
    init_database()

    # Save to SQLite
    for r in working_records:
        insert_failure_record(r, dataset_split="working")

    for r in held_out_records:
        insert_failure_record(r, dataset_split="held_out")

    return working_records, held_out_records
