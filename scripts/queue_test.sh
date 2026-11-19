#!/bin/sh
# Test-split runs in priority order; each stage is resumable and ends with a fresh report,
# so a usable REPORT.md exists after every stage.
set -x
while pgrep -f queue_dev.sh >/dev/null; do sleep 60; done
SG=venv/bin/sg
# 1. primary model, all five main configs, one run                     (~525 episodes)
$SG run main --models qwen2.5-7b --runs 1 && $SG report
# 2. ablations A1-A4 on the primary model                              (~420 episodes)
$SG run ablations --configs "a1_one_hop;a2_no_reversibility;a3_no_rewrites;a4_no_feedback" && $SG report
# 3. the two other local models, all five main configs, one run       (~1050 episodes)
$SG run main --models "llama3.1-8b,mistral-7b" --runs 1 && $SG report
# 4. threshold sweep (A5) and human-approver sensitivity               (~840 episodes)
$SG run ablations && $SG report
# 5. runs 2 and 3 of the main matrix                                   (~3150 episodes)
$SG run main && $SG report
