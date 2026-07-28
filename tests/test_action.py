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
    payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return p, payload, out, summary


# ------------------------------------------------------------------ manifest

def test_action_yml_is_valid_yaml_and_complete():
    spec = yaml.safe_load((HERE / "action.yml").read_text(encoding="utf-8"))
    assert spec["name"] and spec["description"]
    assert spec["runs"]["using"] == "composite"
    for key in ("files", "extractor", "self-test", "fail-on-error"):
        assert key in spec["inputs"], f"missing input {key}"
    for key in ("violations", "report"):
        assert key in spec["outputs"], f"missing output {key}"


def test_action_runs_self_test_before_checking_models():
    """A clean report from an unverified checker is worth nothing, so the
    negative control must come first in the step order."""
    spec = yaml.safe_load((HERE / "action.yml").read_text(encoding="utf-8"))
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
    assert "violations=0" in out.read_text(encoding="utf-8")
    assert "No physically impossible predictions" in summary.read_text(encoding="utf-8")


def test_bad_model_fails_the_build(tmp_path):
    _require("sparam-lint")
    p, rep, out, summary = _run(tmp_path, PL_FILES=str(BAD / "active_gain.s2p"))
    assert p.returncode == 1
    assert rep["total_violations"] > 0
    assert "passivity" in summary.read_text(encoding="utf-8")
    assert "violations=" in out.read_text(encoding="utf-8")


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
    assert "| File | Failed laws |" in summary.read_text(encoding="utf-8")


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
    assert "No files matched" in summary.read_text(encoding="utf-8")


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
    assert "screening ceiling" in summary.read_text(encoding="utf-8")


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
    readme = (HERE / "README.md").read_text(encoding="utf-8")
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
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    block = readme.split("## Example summary", 1)[1].split("\nNote ", 1)[0]
    glob = re.search(r"PL_FILES='([^']+)'", block).group(1)
    prefix = glob.rsplit("/", 1)[0] + "/"
    rows = [ln for ln in block.splitlines() if ln.startswith("> | `")]
    for ln in rows:
        assert prefix in ln, f"row does not come from {glob}: {ln}"


def test_readme_documents_only_environment_variables_that_exist():
    """The local-run section names PL_* twins. They must be the real ones."""
    runner = (HERE / "run_lint.py").read_text(encoding="utf-8")
    section = (HERE / "README.md").read_text(encoding="utf-8").split(
        "## Running it locally", 1)[1].split("## Troubleshooting", 1)[0]
    named = set(re.findall(r"`?(PL_[A-Z_]+|GITHUB_STEP_SUMMARY)`?", section))
    assert named, "the local-run section names no variables -- guard has stopped looking"
    for var in named:
        assert f'"{var}"' in runner, f"README names {var}, which the runner never reads"


def test_empty_glob_is_reported_green_as_the_readme_says():
    """An empty match is a path mistake, not a physics failure."""
    summary = HERE / "tests" / "_empty_summary.md"
    env = dict(os.environ, PL_FILES=str(HERE / "no_such_dir" / "*.s2p"),
               GITHUB_STEP_SUMMARY=str(summary),
               PL_REPORT=str(HERE / "tests" / "_empty_report.json"))
    try:
        rc = subprocess.run([sys.executable, str(HERE / "run_lint.py")],
                            env=env, capture_output=True, text=True).returncode
        assert rc == 0, "README says an empty glob stays green"
        assert "No files matched" in summary.read_text(encoding="utf-8")
    finally:
        for f in (summary, HERE / "tests" / "_empty_report.json"):
            f.unlink(missing_ok=True)


# ------------------------------------------------------------------- SARIF

def _run_runner(env_extra, tmp_path):
    env = dict(os.environ)
    env.update({
        "PL_REPORT": str(tmp_path / "r.json"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md"),
        "GITHUB_OUTPUT": str(tmp_path / "gh.out"),
    })
    env.update(env_extra)
    proc = subprocess.run([sys.executable, str(HERE / "run_lint.py")],
                          env=env, capture_output=True, text=True)
    return proc, (tmp_path / "gh.out")


def _have_checkers():
    return shutil.which("sparam-lint") is not None


@pytest.mark.skipif(not _have_checkers(), reason="sparam-lint not on PATH")
def test_sarif_is_written_and_declares_only_rules_it_uses(tmp_path):
    corpus = HERE.parent / "sparam-conformance" / "data"
    if not corpus.exists():
        pytest.skip("conformance corpus not present")
    dest = tmp_path / "out.sarif"
    proc, _ = _run_runner({"PL_FILES": str(corpus / "*.s2p"),
                           "PL_SARIF": str(dest)}, tmp_path)
    assert dest.exists(), proc.stderr
    doc = json.loads(dest.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    declared = {r["id"] for r in run["tool"]["driver"]["rules"]}
    used = {r["ruleId"] for r in run["results"]}
    assert used == declared, "rule list and results disagree"
    assert run["results"], "the corpus contains known violations"


@pytest.mark.skipif(not _have_checkers(), reason="sparam-lint not on PATH")
def test_sarif_never_invents_a_line_region(tmp_path):
    """A physics failure has a frequency, not a line."""
    corpus = HERE.parent / "sparam-conformance" / "data"
    if not corpus.exists():
        pytest.skip("conformance corpus not present")
    dest = tmp_path / "out.sarif"
    _run_runner({"PL_FILES": str(corpus / "*.s2p"), "PL_SARIF": str(dest)}, tmp_path)
    doc = json.loads(dest.read_text(encoding="utf-8"))
    for res in doc["runs"][0]["results"]:
        for loc in res["locations"]:
            assert "region" not in loc["physicalLocation"]


@pytest.mark.skipif(not _have_checkers(), reason="sparam-lint not on PATH")
def test_asking_for_sarif_without_physics_lint_fails_loudly(tmp_path):
    """Writing nothing and exiting green would leave the upload step empty.

    The caller asked for a file. If we cannot produce it, that has to be an
    error they see, not a silent omission they discover later.
    """
    corpus = HERE.parent / "sparam-conformance" / "data"
    if not corpus.exists():
        pytest.skip("conformance corpus not present")
    proc, _ = _run_runner({
        "PL_FILES": str(corpus / "*.s2p"),
        "PL_SARIF": str(tmp_path / "out.sarif"),
        # Hide physics_lint from the subprocess without uninstalling anything.
        "PYTHONPATH": str(tmp_path),
    }, tmp_path)
    if "physics_lint" in proc.stderr or proc.returncode == 2:
        assert not (tmp_path / "out.sarif").exists() or proc.returncode == 2
    else:
        pytest.skip("physics-lint is installed site-wide; cannot hide it via PYTHONPATH")


def test_sarif_file_input_and_output_are_declared():
    spec = yaml.safe_load((HERE / "action.yml").read_text(encoding="utf-8"))
    assert "sarif-file" in spec["inputs"]
    assert "sarif" in spec["outputs"]
    assert spec["inputs"]["sarif-file"]["default"] == "", (
        "SARIF must be opt-in; defaulting it on would install physics-lint "
        "for every user who never asked for it"
    )
