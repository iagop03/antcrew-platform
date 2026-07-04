"""Tests for run/ticket service helpers."""
from __future__ import annotations

import pytest

from app.models.run import Run, Ticket
from app.services.runs import upsert_tickets_from_run, list_tickets, get_run


async def test_upsert_tickets_from_run_creates(session):
    state = {
        "prd": {"title": "Auth Module"},
        "tickets": [
            {"id": "TICKET-03CE2BBF", "title": "Create login endpoint",
             "description": "POST /login", "priority": "high", "status": "open"},
        ],
    }
    count = await upsert_tickets_from_run(session, "run1", state)
    assert count == 1

    tickets = await list_tickets(session)
    assert len(tickets) == 1
    assert tickets[0].ticket_id == "TICKET-03CE2BBF"
    assert tickets[0].prd_title == "Auth Module"


async def test_upsert_tickets_idempotent(session):
    state = {
        "prd": {"title": "Auth"},
        "tickets": [
            {"id": "TICKET-AAAABBBB", "title": "Login", "description": "",
             "priority": "medium", "status": "open"},
        ],
    }
    await upsert_tickets_from_run(session, "run1", state)
    await upsert_tickets_from_run(session, "run2", state)  # same ticket_id

    tickets = await list_tickets(session)
    assert len(tickets) == 1  # no duplicate
    assert tickets[0].run_id == "run2"  # updated to latest run


async def test_upsert_tickets_updates_status(session):
    state1 = {
        "tickets": [{"id": "TICKET-X1", "title": "T", "description": "",
                     "priority": "low", "status": "open"}],
    }
    state2 = {
        "tickets": [{"id": "TICKET-X1", "title": "T", "description": "",
                     "priority": "low", "status": "done"}],
    }
    await upsert_tickets_from_run(session, "r1", state1)
    await upsert_tickets_from_run(session, "r2", state2)

    tickets = await list_tickets(session)
    assert tickets[0].status == "done"


async def test_upsert_skips_tickets_without_id(session):
    state = {
        "tickets": [{"title": "No ID ticket", "description": "", "priority": "low", "status": "open"}],
    }
    count = await upsert_tickets_from_run(session, "r1", state)
    assert count == 0


async def test_list_tickets_filter_by_status(session):
    session.add(Ticket(ticket_id="T1", run_id="r1", title="a", status="open"))
    session.add(Ticket(ticket_id="T2", run_id="r1", title="b", status="done"))
    await session.commit()

    open_tickets = await list_tickets(session, status="open")
    assert len(open_tickets) == 1
    assert open_tickets[0].ticket_id == "T1"
