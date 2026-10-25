"""Run the offline evaluation harness and print the per-axis report.

Usage: python -m scripts.run_eval [--json]
No network access or paid API calls. See backend/eval/README.md for scope.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.fixtures import FIXTURES, LABELED_BY
from eval.harness import run_all


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def main() -> None:
    report = run_all(FIXTURES)
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return

    print("Skill Passport offline evaluation (fixture-based; not real-world accuracy)")
    print(f"Labels: {LABELED_BY}")
    print(f"Evaluated: {'; '.join(report['scope']['evaluated'])}")
    print(f"Excluded: {'; '.join(report['scope']['excluded'])}")
    print()
    for result in report["fixtures"]:
        sampling = result["sampling"]
        print(f"[{result['split']}] {result['fixture_id']} "
              f"(prompt files {sampling['files_in_prompt']}/{sampling['source_files_total']})")
        for comp in result["comparisons"]:
            marker = "ok" if comp["exact"] else comp["direction"]
            print(f"    {comp['skill_id']:<14} expected {comp['expected']:<12} got {comp['actual']:<12} {marker}")
        for violation in result["absent_violations"]:
            print(f"    {violation['skill_id']:<14} expected absent       got {violation['actual']:<12} FALSE POSITIVE")
        for dropped in sampling["claims_dropped_by_sampling"]:
            print(f"    sampling miss: {dropped['skill_id']} evidence in {dropped['file_path']} never reached the prompt")
    print()
    for split in ("tune", "heldout", "challenge", "all"):
        summary = report["summary"][split]
        detection = summary["detection"]
        levels = summary["levels"]
        print(f"{split.upper()} (fixtures={summary['fixtures']}, labeled skills={summary['labeled_skills']})")
        print(f"  detection: precision {_pct(detection['precision'])} "
              f"recall {_pct(detection['recall'])} F1 {_pct(detection['f1'])} "
              f"(tp={detection['tp']} fp={detection['fp']} fn={detection['fn']})")
        print(f"  levels: exact {_pct(levels['exact_rate'])} within-one {_pct(levels['within_one_rate'])} "
              f"overrated={levels['overrated']} underrated={levels['underrated']} of {levels['total']}")
        print(f"  absent-skill violations: {summary['absent_violations']}; "
              f"claims lost to sampling: {summary['claims_dropped_by_sampling']}")


if __name__ == "__main__":
    main()
