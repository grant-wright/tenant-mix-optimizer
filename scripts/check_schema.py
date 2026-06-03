#!/usr/bin/env python
"""
Schema / data-contract check — guards against train/serve skew.

Validates each given dataset against the contract in `features.py`
(REQUIRED_*_FIELDS) and, if two are given, asserts their observation schemas
are identical. Exits non-zero on any problem, so it works as a smoke test or a
pre-commit hook. Day 5 will also point it at MongoDB-sourced records.

Usage:
    python scripts/check_schema.py data
    python scripts/check_schema.py data data_train     # also assert alignment
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import load_dataset, validate_dataset, assert_aligned  # noqa: E402


def main(argv):
    if not argv:
        print("usage: python scripts/check_schema.py <data_dir> [<other_data_dir>]")
        return 2

    datasets = {}
    problems = []
    for data_dir in argv:
        tenants, observations = load_dataset(data_dir)
        datasets[data_dir] = observations
        ds_problems = validate_dataset(tenants, observations, name=data_dir)
        problems.extend(ds_problems)
        status = "OK" if not ds_problems else "PROBLEMS"
        print(f"[{status}] {data_dir}: {len(tenants)} tenants, {len(observations)} observations")
        for p in ds_problems:
            print(f"    - {p}")

    if len(argv) == 2:
        try:
            assert_aligned(argv[0], datasets[argv[0]], argv[1], datasets[argv[1]])
            print(f"[OK] '{argv[0]}' and '{argv[1]}' observation schemas are aligned")
        except ValueError as e:
            print(f"[PROBLEMS] {e}")
            problems.append(str(e))

    if problems:
        print(f"\nFAILED: {len(problems)} schema problem(s).")
        return 1
    print("\nPASS: schema contract satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
