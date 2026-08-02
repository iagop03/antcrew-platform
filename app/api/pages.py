"""HTML page routes — serves the single-page application shell files."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

_STATIC = Path(__file__).parent.parent / "static"

router = APIRouter(tags=["pages"])


def _html(name: str) -> FileResponse:
    return FileResponse(_STATIC / name, media_type="text/html; charset=utf-8")


@router.get("/login")
async def login_page():
    return _html("login.html")


@router.get("/trial")
async def trial_page():
    return _html("trial.html")


@router.get("/")
async def landing():
    return _html("landing.html")


@router.get("/dashboard")
async def dashboard():
    return _html("index.html")


@router.get("/run/{run_id}")
async def run_detail(run_id: str):
    return _html("run.html")


@router.get("/tickets")
async def tickets_page():
    return _html("tickets.html")


@router.get("/reviews")
async def reviews_page():
    return _html("reviews.html")


@router.get("/evals")
async def evals_page():
    return _html("evals.html")


@router.get("/webhooks")
async def webhooks_page():
    return _html("webhooks.html")


@router.get("/onboard")
async def onboard_page():
    return _html("onboard.html")


@router.get("/settings")
async def settings_page():
    return _html("settings.html")


@router.get("/pipelines")
async def pipelines_page():
    return _html("pipelines.html")


@router.get("/runs")
async def runs_page():
    return _html("runs.html")


@router.get("/compare")
async def compare_page():
    return _html("compare.html")


@router.get("/compare/{compare_id}")
async def compare_detail_page(compare_id: str):
    return _html("compare.html")


@router.get("/admin")
async def admin_page():
    return _html("admin.html")


@router.get("/sw.js")
async def service_worker():
    content = (_STATIC / "sw.js").read_bytes()
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )
