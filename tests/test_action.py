"""Tests for the Physics Lint Action runner.

An Action you cannot run locally is an Action you cannot debug, so the runner
is a plain script and these tests exercise it exactly as CI does -- by setting
the same environment variables and reading the same outputs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parents[1]
# Fixtures live in this repository so the suite is self-contained: a test that
# reaches into a sibling checkout silently passes when the glob matches nothing.
CLEAN = HERE / "fixtures" / "clean"
BAD = HERE / "fixtures" / "bad"
RUNNER = HERE / "run_lint.py"


def _search_path() -> str:
    """The PATH the runner subprocess will see.

    The runner shells out to the `sparam-lint` / `maxwell-lint` console scripts,
    so whatever venv is running pytest has to be reachable. Derive that from the
    interpreter rather than hardcoding a path -- a hardcoded venv passes on the
    machine that wrote it and nowhere else.
    """
    bindir = str(Path(sys.executable).parent)
    return f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}"


def _env(**kw):
    """CI-like environment: the installed console scripts must be on PATH."""
    # Inherit the OS environment and override only what the runner needs.
    # A hand-built env is not portable: on Windows, Python aborts at startup
    # without SYSTEMROOT.
    e = dict(os.environ)
    e["PATH"] = _search_path()
    e.update({k: str(v) for k, v in kw.items()})
    return e


def _require(*tools):
    """Skip with a readable reason when a checker is not installed.

    This package is meant to be splittable into its own repository, where the
    sibling checkouts are gone and the checkers come from PyPI. A missing tool
    should say so rather than surface as an opaque subprocess failure -- and CI
    asserts the tools ARE present, so these skips cannot hide a broken install.
    """
    missing = [t for t in tools if shutil.which(t, path=_search_path()) is None]
    if missing:
        pytest.skip(f"not installed: {', '.join(missing)}")


def _run(tmp_path, **kw):
    report = tmp_path / "report.json"
    out = tmp_path / "gh_out"
    summary = tmp_path / "gh_summary"
    env = _env(PL_REPORT=report, GITHUB_OUTPUT=out, GITHUB_STEP_SUMMARY=summary, **kw)
    p = subprocess.run([sys.executable, str(RUNNER)], capture_output=True,
                       text=True, env=env, cwd=tmp_path)
    payload = json.loads(report.read_text()) if report.exists() else None
    return p, payload, out, summary


# ------------------------------------------------------------------ manifest

def test_action_yml_is_valid_yaml_and_complete():
    spec = yaml.safe_load((HERE / "action.yml").read_text())
    assert spec["name"] and spec["description"]
    assert spec["runs"]["using"] == "composite"
    for key in ("files", "extractor", "self-test", "fail-on-error"):
        assert key in spec["inputs"], f"missing input {key}"
    for key in ("violations", "report"):
        assert key in spec["outputs"], f"missing output {key}"


def test_action_runs_self_test_before_checking_models():
    """A clean report from an unverified checker is worth nothing, so the
    negative control must come first in the step order."""
    spec = yaml.safe_load((HERE / "action.yml").read_text())
    names = [s.get("name", "") for s in spec["runs"]["steps"]]
    i_self = next(i for i, n in enumerate(names) if "discriminate" in n.lower())
    i_run = next(i for i, n in enumerate(names) if "physics lint" in n.lower())
    assert i_self < i_run, "self-test must precede the model check"


# ------------------------------------------------------------------- runner

def test_no_inputs_is_a_noop(tmp_path):
    p, _, _, _ = _run(tmp_path, PL_FILES="", PL_EXTRACTOR="")
    assert p.returncode == 0
    assert "nothing to do" in p.stderr


def test_clean_models_pass(tmp_path):
    _require("sparam-lint")
    p, rep, out, summary = _run(tmp_path, PL_FILES=str(CLEAN / "passive_line.s2p"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert rep["total_violations"] == 0
    assert "violations=0" in out.read_text()
    assert "No physically impossible predictions" in summary.read_text()


def test_bad_model_fails_the_build(tmp_path):
    _require("sparam-lint")
    p, rep, out, summary = _run(tmp_path, PL_FILES=str(BAD / "active_gain.s2p"))
    assert p.returncode == 1
    assert rep["total_violations"] > 0
    assert "passivity" in summary.read_text()
    assert "violations=" in out.read_text()


def test_fail_on_error_false_reports_without_failing(tmp_path):
    _require("sparam-lint")
    p, rep, _, _ = _run(tmp_path, PL_FILES=str(BAD / "active_gain.s2p"),
                        PL_FAIL="false")
    assert p.returncode == 0
    assert rep["total_violations"] > 0, "violations must still be reported"


def test_glob_matches_multiple_files_and_reports_each(tmp_path):
    """A glob spanning clean and bad models must check all of them and fail."""
    _require("sparam-lint")
    p, rep, _, summary = _run(tmp_path, PL_FILES=str(HERE / "fixtures" / "*" / "*.s2p"))
    assert rep["sparam"]["n_files"] >= 2, "the glob matched nothing -- fixtures moved?"
    assert p.returncode == 1, "the fixtures include a known-bad model"
    assert "| File | Failed laws |" in summary.read_text()


def test_unparseable_file_is_an_error_not_a_pass(tmp_path):
    _require("sparam-lint")
    bad = tmp_path / "broken.s2p"
    bad.write_text("# HZ S RI R 50\n1e9 nan 0 0.5 0 0.5 0 0.1 0\n")
    p, rep, _, _ = _run(tmp_path, PL_FILES=str(bad))
    assert p.returncode == 2, "a file that cannot be parsed must not silently pass"
    assert rep["total_errors"] == 1


def test_no_matching_files_is_not_a_failure(tmp_path):
    _require("sparam-lint")
    p, rep, _, summary = _run(tmp_path, PL_FILES=str(tmp_path / "nothing*.s2p"))
    assert p.returncode == 0
    assert rep["sparam"]["n_files"] == 0
    assert "No files matched" in summary.read_text()


def test_extractor_check_passes_for_a_sound_extractor(tmp_path):
    _require("maxwell-lint")
    p, rep, _, _ = _run(tmp_path,
                        PL_EXTRACTOR="maxwell_lint.models:monopole_closure")
    assert p.returncode == 0, p.stdout
    assert rep["maxwell"]["violations"] == 0


def test_extractor_check_fails_for_an_unphysical_extractor(tmp_path):
    _require("maxwell-lint")
    p, rep, _, summary = _run(tmp_path,
                              PL_EXTRACTOR="maxwell_lint.models:born_second_order")
    assert p.returncode == 1
    assert rep["maxwell"]["violations"] > 0
    assert "screening ceiling" in summary.read_text()


def test_both_checks_combine(tmp_path):
    _require("sparam-lint", "maxwell-lint")
    p, rep, _, _ = _run(tmp_path,
                        PL_FILES=str(CLEAN / "passive_line.s2p"),
                        PL_EXTRACTOR="maxwell_lint.models:monopole_closure")
    assert p.returncode == 0
    assert "sparam" in rep and "maxwell" in rep


def test_report_json_is_written_and_wellformed(tmp_path):
    _require("sparam-lint")
    _, rep, _, _ = _run(tmp_path, PL_FILES=str(CLEAN / "passive_line.s2p"))
    assert set(rep) >= {"total_violations", "total_errors", "sparam"}
    json.dumps(rep)  # must round-trip


# --------------------------------------------- the documented summary must add up

def test_readme_example_summary_is_internally_consistent():
    """The example is generated, but a hand-edit could still desync the counts.

    The table lists one row per failing file and names each failed law, so the
    two headline numbers -- files with violations, and total violations -- are
    both derivable from the table itself. If someone tweaks a number without
    regenerating, this goes red.
    """
    readme = (HERE / "README.md").read_text()
    block = readme.split("## Example summary", 1)[1].split("\nNote ", 1)[0]

    rows = [ln for ln in block.splitlines()
            if ln.startswith("> | `") and ".s2p`" in ln]
    laws = sum(len(ln.split("|")[2].split(",")) for ln in rows)

    n_with = int(re.search(r"(\d+) with violations", block).group(1))
    n_viol = int(re.search(r"\*\*(\d+) violation\(s\)", block).group(1))

    assert len(rows) == n_with, f"{len(rows)} rows listed, headline says {n_with}"
    assert laws == n_viol, f"table names {laws} failed laws, headline says {n_viol}"


def test_readme_regeneration_command_matches_the_paths_it_shows():
    """A command whose output does not match the pasted table is worse than none."""
    readme = (HERE / "README.md").read_text()
    block = readme.split("## Example summary", 1)[1].split("\nNote ", 1)[0]
    glob = re.search(r"PL_FILES='([^']+)'", block).group(1)
    prefix = glob.rsplit("/", 1)[0] + "/"
    rows = [ln for ln in block.splitlines() if ln.startswith("> | `")]
    for ln in rows:
        assert prefix in ln, f"row does not come from {glob}: {ln}"
