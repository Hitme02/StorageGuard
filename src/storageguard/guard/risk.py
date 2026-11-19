"""Risk score: reversibility factor times a noisy-OR over the analysers.

    risk = f(R) * (1 - prod_c (1 - w_c * sev_c))

where ``sev_c`` is the worst severity analyser ``c`` reported and ``w_c`` its
configurable weight.  The noisy-OR means independent moderate concerns add up,
while one severe concern alone is enough to dominate.
"""

from __future__ import annotations

from .analyzers import ANALYZERS, Finding
from .config import GuardConfig


def analyzer_severities(findings: list[Finding]) -> dict[str, float]:
    sev = dict.fromkeys(ANALYZERS, 0.0)
    for f in findings:
        sev[f.analyzer] = max(sev[f.analyzer], f.severity)
    return sev


def risk_score(findings: list[Finding], rclass: int, cfg: GuardConfig) -> float:
    survive = 1.0
    for analyzer, sev in analyzer_severities(findings).items():
        survive *= 1.0 - min(1.0, cfg.weights.get(analyzer, 1.0) * sev)
    factor = cfg.reversibility_factors[rclass] if cfg.use_reversibility else 1.0
    return round(factor * (1.0 - survive), 4)
