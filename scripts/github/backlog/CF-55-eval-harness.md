<!-- title: CF-55 · Model evaluation harness (time-based clip quality) -->
<!-- labels: eval, feat, P1 -->
In-repo benchmark that scores a pipeline run against hand-labeled highlights for a fixed video, as threshold-free numbers tracked across model versions. Absolute values don't matter — deltas between tagged runs do.

**Approach:** time-based interval arithmetic (merge/intersect/subtract), NOT clip-matching rules. Every run computes signals **pre-gate and post-gate**.

**Signals:** Captured % (model ∩ human / human seconds) · Play buckets (per human clip: well ≥50% / butchered 0–50% / missed 0) · Incorrect time split into lead-slop / tail-slop / bridge / junk · Score-separation AUC (does `highlight_score` rank capturing windows above junk). Lead/tail slop is expected near the design pads — track drift beyond them.

**Shape:** `ml/eval/{metrics.py (pure), harness.py, fixtures/, results/*.jsonl, README.md}` + `ml/tests/test_eval_metrics.py`. CLI: `python -m ml.eval.harness --test test1 --version <label>` prints signals + per-clip audit, appends a results row (with config snapshot).

**Acceptance:** pure metrics unit-tested; runs end-to-end against CF-56's first fixture; baseline row committed + posted in PR; adding a case = one fixture file. Extend CI/pre-commit to lint `ml/`.

**Depends:** CF-56's first fixture for final acceptance.
