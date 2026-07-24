"""Domain exceptions for antcrew-platform.

All subclass HTTPException so FastAPI handles them identically to inline
``raise HTTPException(...)`` calls — no exception handlers needed in main.py.
The benefit is consistent error messages, importable symbols for tests, and
a single place to change status codes if the API versioning policy changes.
"""
from __future__ import annotations

from fastapi import HTTPException


class RunNotFoundError(HTTPException):
    def __init__(self, run_id: str) -> None:
        super().__init__(404, f"Run {run_id!r} not found")


class RunNotAccessibleError(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "This run is not accessible with the current API key")


class NotAccessibleError(HTTPException):
    def __init__(self) -> None:
        super().__init__(403, "Not accessible with the current API key")


class RunNotRunningError(HTTPException):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(409, f"Run {run_id!r} is not running (status: {status!r})")


class StateNotAvailableError(HTTPException):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(404, f"State not available yet — run {run_id!r} is still {status!r}")


class ReviewNotFoundError(HTTPException):
    def __init__(self, review_id: str) -> None:
        super().__init__(404, f"Review {review_id!r} not found")


class WorkspaceNotFoundError(HTTPException):
    def __init__(self, workspace_id: int | str) -> None:
        super().__init__(404, f"Workspace {workspace_id!r} not found")


class BudgetExceededError(HTTPException):
    def __init__(self, workspace_id: int | None = None) -> None:
        detail = (
            f"Workspace {workspace_id} budget exceeded"
            if workspace_id is not None
            else "Workspace budget exceeded"
        )
        super().__init__(402, detail)


class InvalidTeamError(HTTPException):
    def __init__(self, team: str, available: list[str]) -> None:
        super().__init__(422, f"Unknown team {team!r}. Available: {available}")


class CompareNotFoundError(HTTPException):
    def __init__(self, compare_id: str) -> None:
        super().__init__(404, f"Comparison {compare_id!r} not found")


class TicketNotFoundError(HTTPException):
    def __init__(self, ticket_id: str) -> None:
        super().__init__(404, f"Ticket {ticket_id!r} not found")


class EvalNotFoundError(HTTPException):
    def __init__(self, eval_id: str) -> None:
        super().__init__(404, f"Eval {eval_id!r} not found")
