"""Parse an `agent-spec lifecycle --format json` output and classify
the result.

Agent-spec's verify layer has three kinds of findings on any given
spec and we treat each with different CI severity. Matches the
upstream `contract-guard.yml` model (they run with
``continue-on-error: true`` for the same reasons).

Hard-fail (exits 1):
  * JSON parse error — agent-spec itself failed unexpectedly.
  * quality_score below --min-score — sloppy spec authoring.
  * scenario with verdict=fail that is NOT a boundary pseudo-scenario —
    future-proof for when an AI backend lands and scenarios actually
    verify; a real scenario failure is a real bug.

Soft (reported, exits 0):
  * boundary violations — the pseudo-scenario whose name starts with
    ``[boundaries]``. Semantically a spec's Allowed list only makes
    sense for the one PR that owns that spec; other specs in the same
    repo always flag cross-cutting changes. We report the finding but
    do not block the build. When an issue-to-spec association lands
    upstream, this can move to hard-fail.
  * scenario verify skips — expected without an AI backend per
    docs/dev/agent-spec.md.

Every run prints a single ``status=... spec=... ...`` summary line on
stdout so the calling shell can aggregate.
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, help="spec path (for log output)")
    parser.add_argument("--min-score", type=float, default=0.6)
    parser.add_argument(
        "--owning-spec",
        default="",
        help="If set and equal to --spec, boundary violations hard-fail; "
        "otherwise they are soft-reported. See scripts/_detect_owning_spec.py.",
    )
    args = parser.parse_args()

    raw = sys.stdin.read()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        print(f"status=PARSE_ERROR spec={args.spec}")
        return 1

    lint_issues = doc.get("lint_issues", 0)
    quality = float(doc.get("quality_score", 0.0))
    ver = doc.get("verification") or {}
    results = ver.get("results") or []

    boundary_fails = [
        r for r in results if r.get("verdict") == "fail" and str(r.get("scenario_name", "")).startswith("[boundaries]")
    ]
    scenario_fails = [
        r
        for r in results
        if r.get("verdict") == "fail" and not str(r.get("scenario_name", "")).startswith("[boundaries]")
    ]
    skips = [r for r in results if r.get("verdict") == "skip"]

    hard_reasons: list[str] = []
    soft_reasons: list[str] = []
    if quality < args.min_score:
        hard_reasons.append(f"quality={quality:.2f}<min={args.min_score:.2f}")
    if scenario_fails:
        names = ",".join(str(r.get("scenario_name")) for r in scenario_fails)
        hard_reasons.append(f"scenario_fail({names})")
    if boundary_fails:
        if args.owning_spec and args.owning_spec == args.spec:
            # This PR owns this spec — boundary violations are real.
            hard_reasons.append(f"boundary_fail={len(boundary_fails)}")
        else:
            # Cross-cutting PR; this spec does not own the change.
            soft_reasons.append(f"boundary_fail={len(boundary_fails)}")

    status = "HARD_FAIL" if hard_reasons else "OK"
    reason_parts = []
    if hard_reasons:
        reason_parts.append("hard:" + "|".join(hard_reasons))
    if soft_reasons:
        reason_parts.append("soft:" + "|".join(soft_reasons))
    reasons_str = " ".join(reason_parts) or "-"

    print(
        f"status={status} spec={args.spec} lint_issues={lint_issues} "
        f"quality={quality:.2f} skipped={len(skips)} {reasons_str}"
    )
    return 1 if hard_reasons else 0


if __name__ == "__main__":
    raise SystemExit(main())
