# Contributing to physics-lint-action

The one non-negotiable rule:

> **A file that could not be parsed is never reported as a pass.**

Exit `2` (could not check) deliberately outranks exit `1` (checked and
failed). "I could not read this" is a worse answer than "I read it and it
failed", so it wins the exit code. A checker that skips what it cannot read
silently approves it, and in CI a green check is read as validation.

## What a good contribution looks like

- **Another way for the Action to be honest about what it did not check.** An
  input class that currently slips through the glob, a failure mode reported as
  a warning that should fail the build.
- **A summary or SARIF improvement that does not invent information.** Results
  carry no line `region`, because a physics failure happens at a *frequency*
  and a Touchstone file's rows are not the unit anyone reasons about. A test
  enforces the omission. Making the annotation prettier by fabricating a
  location will be turned down.
- **A local-runnability fix.** `run_lint.py` is a plain script with no CI-only
  magic — every input has an environment-variable twin — because an Action you
  cannot run locally is one you cannot debug. Keep it that way.

## Before you open a PR

```bash
pip install pytest pyyaml ruff
python -m pytest tests/ -q
ruff check .

# and run it the way the Action does
PL_FILES='models/**/*.s2p' GITHUB_STEP_SUMMARY=/dev/stdout python run_lint.py
```

`action.yml`'s inputs and the README's input table are checked against each
other in both directions by a test, so if you add an input, document it in the
same change or the suite will tell you.
