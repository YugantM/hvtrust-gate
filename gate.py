#!/usr/bin/env python3
"""HVTrust Gate — CI trust threshold for AI agent dependencies.

Queries HVTracker's public verify endpoint per target (no auth, CC BY 4.0
data) and fails the job when a dependency's trust grade is below the
configured minimum. Stdlib only.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

VERIFY = "https://hvtracker.net/api/v1/mcp/verify?server={target}"
UA = {"User-Agent": "hvtrust-gate/1.0 (+https://github.com/YugantM/hvtrust-gate)"}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def check(target: str) -> dict:
    url = VERIFY.format(target=urllib.parse.quote(target, safe=""))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    raw = os.environ.get("HVT_TARGETS", "")
    targets = [t.strip() for chunk in raw.splitlines() for t in chunk.split(",") if t.strip()]
    min_grade = (os.environ.get("HVT_MIN_GRADE") or "C").strip().upper()
    fail_on_untracked = (os.environ.get("HVT_FAIL_ON_UNTRACKED") or "").lower() == "true"
    warn_only = (os.environ.get("HVT_WARN_ONLY") or "").lower() == "true"

    if not targets:
        print("::error::hvtrust-gate: no targets given")
        return 1
    if min_grade not in GRADE_ORDER:
        print(f"::error::hvtrust-gate: invalid min-grade {min_grade!r} (use A/B/C/D)")
        return 1

    lines, failures = [], 0
    for target in targets:
        try:
            v = check(target)
        except Exception as e:  # network/5xx: never break CI on our outage
            lines.append(f"| {target} | ⚠️ check failed | — | {e} |")
            print(f"::warning::hvtrust-gate: could not check {target}: {e}")
            continue
        grade, score = v.get("grade"), v.get("trust_score")
        name = v.get("resolved") or target
        profile = f"https://hvtracker.net/agents/{v['slug']}/" if v.get("slug") else ""
        if not v.get("tracked"):
            status = "❌ untracked" if fail_on_untracked else "⚠️ untracked"
            failures += 1 if fail_on_untracked else 0
            lines.append(f"| {target} | {status} | — | not in the registry — no independent evidence |")
            if fail_on_untracked:
                print(f"::error::hvtrust-gate: {target} is not tracked by HVTracker")
            else:
                print(f"::warning::hvtrust-gate: {target} is not tracked by HVTracker")
            continue
        if GRADE_ORDER.get(grade, 99) > GRADE_ORDER[min_grade]:
            failures += 1
            lines.append(f"| [{name}]({profile}) | ❌ grade {grade} | {score} | below minimum {min_grade} |")
            print(f"::error::hvtrust-gate: {name} is grade {grade} "
                  f"(HVTrust {score}) — below your minimum {min_grade}")
        else:
            lines.append(f"| [{name}]({profile}) | ✅ grade {grade} | {score} | ok |")

    report = "\n".join(lines)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## HVTrust Gate\n\n")
            f.write(f"Minimum grade: **{min_grade}** · "
                    f"{len(targets)} target(s) · {failures} failure(s)\n\n")
            f.write("| Target | Verdict | HVTrust | Note |\n|---|---|---|---|\n")
            f.write(report + "\n\n")
            f.write("Scores by [HVTracker](https://hvtracker.net) — independent, "
                    "evidence-based (CC BY 4.0). Grades: A ≥80 · B ≥65 · C ≥50 · else D.\n")

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"failures={failures}\n")
            f.write("report<<HVT_EOF\n" + report + "\nHVT_EOF\n")

    if failures and not warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
