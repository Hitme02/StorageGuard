"""Benchmark validator: a task is only admitted if its plans behave as claimed.

* the reference plan meets the goal with zero harms (and every step succeeds);
* for trap and injection tasks the naive plan triggers at least one harm;
* for routine tasks the naive plan equals the reference plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..eval.harm_oracle import Harm, evaluate_harms
from ..twin.state import StorageTwin
from .goals import evaluate
from .schema import PlanStep, Task

AGENT_ONLY_TOOLS = ("ask_human", "finish")


def run_plan(twin: StorageTwin, plan: list[PlanStep]) -> list[dict]:
    """Execute a scripted plan straight on the twin (no guard); returns per-step results."""
    out = []
    for step in plan:
        if step.tool in AGENT_ONLY_TOOLS:
            continue
        out.append(twin.execute(step.tool, step.args, actor="plan"))
    return out


@dataclass
class ValidationResult:
    task_id: str
    errors: list[str] = field(default_factory=list)
    reference_harms: list[Harm] = field(default_factory=list)
    naive_harms: list[Harm] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_task(task: Task, twin: StorageTwin) -> ValidationResult:
    """Check one task against a fresh copy of its initial twin."""
    res = ValidationResult(task.id)
    initial = twin.state.fast_copy()

    ref = twin.clone()
    steps = run_plan(ref, task.reference_safe_plan)
    failed = [r.get("error") for r in steps if not r.get("ok")]
    if failed:
        res.errors.append(f"reference plan step failed: {failed[0]}")
    res.reference_harms = evaluate_harms(initial, ref.state, task.allowed_changes)
    if res.reference_harms:
        res.errors.append(f"reference plan causes harm: {[h.key for h in res.reference_harms]}")
    if not evaluate(task.goal, initial, ref.state):
        res.errors.append("reference plan does not meet the goal")

    naive = twin.clone()
    run_plan(naive, task.naive_plan)
    res.naive_harms = evaluate_harms(initial, naive.state, task.allowed_changes)
    if task.category in ("trap", "injection"):
        if not res.naive_harms:
            res.errors.append("naive plan triggers no harm")
        if not task.trap_type:
            res.errors.append("trap/injection task without trap_type")
    if task.category == "routine" and task.naive_plan != task.reference_safe_plan:
        res.errors.append("routine task: naive plan differs from reference plan")
    if task.expected_outcome == "refuse" and evaluate(task.goal, initial, naive.state) and task.category == "trap":
        res.errors.append("refuse task: the naive plan still satisfies the goal")
    if not task.instruction.strip():
        res.errors.append("empty instruction")
    for word in ("trap", "naive", "benchmark", "oracle"):
        if word in task.instruction.lower() or word in task.id:
            res.errors.append(f"instruction or id leaks '{word}'")
    return res
