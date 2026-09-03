"""
Script to generate synthetic dataset and populate working & held-out sets.
Usage:
    python scripts/generate_dataset.py
"""
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.data.synthetic_generator import generate_synthetic_dataset
from src.data.dataset_splitter import split_and_persist_dataset
from src.database import get_failure_records


def main():
    print("=" * 60)
    print("Mandate Recovery Agent — Generating Synthetic Dataset")
    print("=" * 60)

    total_records_to_generate = 200
    print(f"Generating {total_records_to_generate} realistic mandate failure records...")
    records = generate_synthetic_dataset(total_count=total_records_to_generate, seed=42)

    print(f"Generated {len(records)} records.")
    print("Splitting into 70% Working Set and 30% Held-Out Set...")
    working, held_out = split_and_persist_dataset(records, split_ratio=0.70, seed=42)

    print(f"✓ Working set created:  {len(working)} records (saved to data/working_set.json)")
    print(f"✓ Held-out set created: {len(held_out)} records (saved to data/held_out_set.json)")

    # Verify SQLite database
    db_working = get_failure_records("working")
    db_held_out = get_failure_records("held_out")
    print(f"✓ SQLite database synced: {len(db_working)} working, {len(db_held_out)} held-out records in database.")

    # Calculate and display category breakdown
    print("\nDecline Reason Distribution in Generated Dataset:")
    print("-" * 55)
    counts = {}
    for r in records:
        reason = r.decline_reason.value
        counts[reason] = counts.get(reason, 0) + 1

    for reason, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        pct = (count / len(records)) * 100
        print(f"  {reason:<26}: {count:>3} records ({pct:>5.1f}%)")

    print("-" * 55)
    print("Dataset generation complete and verified successfully!")


if __name__ == "__main__":
    main()
