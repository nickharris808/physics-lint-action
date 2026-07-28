"""Runner for the Physics Lint GitHub Action.

Kept as a plain script with no dependencies beyond the checkers themselves, so
it is testable outside CI -- the whole point being that an Action you cannot run
locally is an Action you cannot debug.

Environment:
    PL_FILES       glob of Touchstone files ('' disables)
    PL_EXTRACTOR   module:function of a coupling extractor ('' disables)
    PL_FAIL        'true' to exit non-zero on violations
    GITHUB_OUTPUT  written if present (Action outputs)
    GITHUB_STEP_SUMMARY  written if present (the PR summary table)
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path


def _sh(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_files(pattern: str) -> dict:
    paths = sorted(glob.glob(pattern, recursive=True))
    results, violations, errors = [], 0, 0
    for path in paths:
        rc, out = _sh(["sparam-lint", path, "--json"])
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            errors += 1
            results.append({"file": path, "error": out.strip()[:400]})
            continue
        if rc == 2 or "error" in payload:
            errors += 1
            results.append({"file": path, "error": payload.get("error", "parse failed")})
            continue
        failed = [law["law"] for law in payload.get("laws", []) if not law["passed"]]
        violations += len(failed)
        results.append({"file": path, "passed": not failed, "failed_laws": failed})
    return {"pattern": pattern, "n_files": len(paths), "violations": violations,
            "errors": errors, "results": results}


def check_extractor(spec: str) -> dict:
    rc, out = _sh(["maxwell-lint", "check", "--extractor", spec, "--json"])
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {"extractor": spec, "error": out.strip()[:400], "violations": 0, "errors": 1}
    return {"extractor": spec,
            "violations": int(payload.get("total_violations", 0)),
            "total_pairs": payload.get("total_pairs"),
            "max_k": payload.get("max_k"),
            "errors": 0}


def summary_md(report: dict) -> str:
    lines = ["## Physics Lint", ""]
    sp = report.get("sparam")
    if sp:
        if sp["n_files"] == 0:
            lines.append(f"No files matched `{sp['pattern']}`.")
        else:
            bad = [r for r in sp["results"] if not r.get("passed", True)]
            lines.append(f"**S-parameter models** — {sp['n_files']} file(s) checked, "
                         f"{len(bad)} with violations.")
            if bad:
                lines += ["", "| File | Failed laws |", "|---|---|"]
                lines += [f"| `{r['file']}` | {', '.join(r.get('failed_laws', []))} |"
                          for r in bad]
        lines.append("")
    mx = report.get("maxwell")
    if mx:
        if mx.get("error"):
            lines.append(f"**Coupling extractor** — error: `{mx['error']}`")
        else:
            lines.append(
                f"**Coupling extractor `{mx['extractor']}`** — "
                f"{mx['violations']}/{mx.get('total_pairs')} pairs violate the "
                f"screening ceiling (max k = {mx.get('max_k')}).")
        lines.append("")
    total = report["total_violations"]
    lines.append(
        "✅ No physically impossible predictions found." if total == 0
        else f"❌ **{total} violation(s).** These models describe behaviour that "
             "cannot occur in a passive linear system.")
    return "\n".join(lines)


def main() -> int:
    files = os.environ.get("PL_FILES", "").strip()
    extractor = os.environ.get("PL_EXTRACTOR", "").strip()
    fail_on = os.environ.get("PL_FAIL", "true").lower() == "true"

    if not files and not extractor:
        print("physics-lint: nothing to do -- set `files` and/or `extractor`.",
              file=sys.stderr)
        return 0

    report: dict = {"total_violations": 0, "total_errors": 0}
    if files:
        report["sparam"] = check_files(files)
        report["total_violations"] += report["sparam"]["violations"]
        report["total_errors"] += report["sparam"]["errors"]
    if extractor:
        report["maxwell"] = check_extractor(extractor)
        report["total_violations"] += report["maxwell"]["violations"]
        report["total_errors"] += report["maxwell"]["errors"]

    out_path = Path(os.environ.get("PL_REPORT", "physics-lint-report.json"))
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    md = summary_md(report)
    print(md)

    if (gs := os.environ.get("GITHUB_STEP_SUMMARY")):
        with open(gs, "a") as fh:
            fh.write(md + "\n")
    if (go := os.environ.get("GITHUB_OUTPUT")):
        with open(go, "a") as fh:
            fh.write(f"violations={report['total_violations']}\n")
            fh.write(f"report={out_path}\n")

    if report["total_errors"]:
        return 2
    return 1 if (report["total_violations"] and fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
