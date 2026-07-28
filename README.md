# Physics Lint — GitHub Action

![CI](https://github.com/nickharris808/physics-lint-action/actions/workflows/ci.yml/badge.svg) ![Marketplace](https://img.shields.io/badge/GitHub%20Marketplace-physics--lint-blue) ![Licence](https://img.shields.io/badge/licence-Apache--2.0-green) ![Tests](https://img.shields.io/badge/tests-15%20passing-brightgreen)

**Fail the build when a model predicts physics that cannot exist.**

Hardware teams review RTL in CI and review S-parameter models by eye. This
Action puts the physics check where the code review already happens.

## Quickstart

```yaml
- uses: nickharris808/physics-lint-action@v1
  with:
    files: 'models/**/*.s*p'
```

That is it. On every PR, every Touchstone file is checked against five physical
laws, a summary table is posted to the run, and the build fails if any model
describes a network that cannot exist.

## Also check a coupling extractor

```yaml
- uses: nickharris808/physics-lint-action@v1
  with:
    files: 'models/**/*.s*p'
    extractor: 'mypackage.extract:coupling_matrix'
```

The extractor is tested against the many-body screening ceiling: a predicted
screening factor above 1 means the extractor thinks a grounded conductor
between two others *increases* their coupling.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `files` | `''` | Glob of Touchstone files. Empty disables the S-parameter check. |
| `extractor` | `''` | `module:function` of a coupling extractor. Empty disables. |
| `self-test` | `true` | Run the checker's negative control first. |
| `fail-on-error` | `true` | Set `false` to report without failing. |
| `python-version` | `3.11` | Python used for the checkers. |

## Outputs

| Output | Meaning |
|---|---|
| `violations` | Total violations found |
| `report` | Path to the JSON report |

## Exit behaviour

| Code | Meaning |
|---|---|
| 0 | clean, or violations with `fail-on-error: false` |
| 1 | violations found |
| 2 | a file could not be parsed — **never treated as a pass** |

An unparseable file exits 2 rather than passing. A checker that skips what it
cannot read silently approves it.

## Why `self-test` defaults to true

It runs the checker's negative control — deliberately invalid networks that
each law must reject — *before* checking your models. A clean report from a
checker nobody verified is worth nothing, and a physics checker that has quietly
stopped discriminating looks exactly like a healthy one.

It costs about a second. Leave it on.

## Example summary

Run over a directory of models, this is what lands in the
job summary. It is generated, not illustrated, and you can regenerate it — the
input is the ten 2-port cases from the
[`sparam-conformance`](https://huggingface.co/datasets/nickh007/sparam-conformance)
corpus:

```bash
git clone https://huggingface.co/datasets/nickh007/sparam-conformance
PL_FILES='sparam-conformance/data/*.s2p' GITHUB_STEP_SUMMARY=/dev/stdout python3 run_lint.py
```

> ## Physics Lint
>
> **S-parameter models** — 10 file(s) checked, 5 with violations.
>
> | File | Failed laws |
> |---|---|
> | `sparam-conformance/data/active_gain.s2p` | passivity, energy_conservation |
> | `sparam-conformance/data/energy_row_violation.s2p` | passivity, energy_conservation |
> | `sparam-conformance/data/ferrite_isolator.s2p` | reciprocity |
> | `sparam-conformance/data/negative_resistance.s2p` | passivity, energy_conservation, positive_real_z0 |
> | `sparam-conformance/data/noncausal_advance.s2p` | group_delay_nonneg |
>
> ❌ **9 violation(s).** These models describe behaviour that cannot occur in a passive linear system.

Note `ferrite_isolator`: a real, buyable component whose medium is
non-reciprocal, so `S ≠ Sᵀ` is correct behaviour. The check firing there is a
true positive for the law and a false alarm for the device. Declare
non-reciprocity expected for those files rather than switching the law off.

## Running it locally

The runner is a plain script with no CI-only magic, because an Action you cannot
run locally is one you cannot debug:

```bash
PL_FILES='models/**/*.s2p' python run_lint.py
```

## Scope, honestly

**A green build here does not mean your models are correct.** These checks test
*physical admissibility* — whether a network could exist at all. A perfectly
passive model of entirely the wrong structure passes every one of them.

That matters more in CI than anywhere else, because a passing check is easy to
read as validation. It is not. It is the floor: it catches models that describe
behaviour no passive linear system can produce, and says nothing about whether
the model matches the thing you actually built.

## The rest of the toolkit

Eight artifacts that answer one question in different places: **is this
model physically possible?** Each is a grader — it can tell you a model is
wrong; none can tell you one is right.

| | |
|---|---|
| [`sparam-lint`](https://github.com/nickharris808/sparam-lint) | Is an S-parameter model physically possible? Five laws + a negative control. |
| [`maxwell-lint`](https://github.com/nickharris808/maxwell-lint) | Does a coupling extractor predict impossible physics? Screening ceiling k ≤ 1. |
| [`abstain-bench`](https://github.com/nickharris808/abstain-bench) | Does a model know when to shut up? Abstention recall, never pooled with accuracy. |
| [`sparam-conformance`](https://huggingface.co/datasets/nickh007/sparam-conformance) | 11 labelled networks with verified ground truth. Grades the graders. |
| [`screening-ceiling`](https://huggingface.co/datasets/nickh007/screening-ceiling) | A certified impossibility result + 27 counterexamples. Zero-dependency verifier. |
| [`physics-lint-action`](https://github.com/nickharris808/physics-lint-action) ← you are here | The same checks, in your CI. |
| [`physics-lint-mcp`](https://github.com/nickharris808/physics-lint-mcp) | A physics oracle your AI agent can call. |
| [**Try it in your browser**](https://huggingface.co/spaces/nickh007/physics-lint) | All three checks, no install, runs client-side. |

These tools **grade** a model. Producing one that is passive *by
construction* — so it cannot fail these laws whatever its parameters — and
accurate at speed in the many-body regime, with calibrated abstention and a
fail-closed signoff certificate, is the commercial core:
**[ChipletOS](https://chipletos.com)**.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

