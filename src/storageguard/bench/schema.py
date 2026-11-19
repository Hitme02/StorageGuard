"""Task schema of StorageAgentBench (JSON-serialisable pydantic models)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanStep(_M):
    """One scripted tool call."""

    tool: str
    args: dict[str, Any] = {}


class ToolPattern(_M):
    """A mutating call the ticket legitimately entails: tool name + argument subset."""

    tool: str
    args: dict[str, Any] = {}

    def matches(self, tool: str, args: dict[str, Any]) -> bool:
        return tool == self.tool and all(args.get(k) == v for k, v in self.args.items())


class AllowedChanges(_M):
    """What the ticket authorises.  Read only by the harm oracle and the simulated human.

    * ``tools``: destructive calls within the ticket's scope (the human approves only these).
    * ``deletable_volumes``: volumes that may disappear (their host paths and snapshots with them).
    * ``expendable_data``: volumes whose *data* may be lost for good.
    * ``removable_paths``: (host, volume) access paths that may be removed.
    * ``breakable_links`` / ``pausable_links``: replication links that may be broken / paused.
    """

    tools: list[ToolPattern] = []
    deletable_volumes: list[str] = []
    expendable_data: list[str] = []
    removable_paths: list[tuple[str, str]] = []
    breakable_links: list[str] = []
    pausable_links: list[str] = []

    def permits(self, tool: str, args: dict[str, Any]) -> bool:
        return any(p.matches(tool, args) for p in self.tools)


class Clarification(_M):
    """Scripted answer of the ticket owner; chosen when any keyword occurs in the question."""

    keywords: list[str] = []
    answer: str


Category = Literal["routine", "ambiguous", "trap", "injection"]
ExpectedOutcome = Literal["complete", "ask", "refuse", "partial_safe"]


class Task(_M):
    """One benchmark task.  Only ``instruction`` is ever shown to the agent."""

    id: str
    category: Category
    trap_type: str | None = None  # T1..T12, P1..P3, or None
    task_type: str  # generator recipe (internal bookkeeping)
    scenario_seed: int
    instruction: str
    clarifications: list[Clarification] = []
    goal: dict[str, Any]
    allowed_changes: AllowedChanges = AllowedChanges()
    expected_outcome: ExpectedOutcome = "complete"
    reference_safe_plan: list[PlanStep] = []
    naive_plan: list[PlanStep] = []
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    notes: str = ""
    split: Literal["dev", "test"] | None = None
    state_hash: str = ""
