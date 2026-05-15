"""Report API routes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/daily")
async def daily_report(request: Request, target_date: str = Query("", alias="date")):
    reporter = request.app.state.reporter
    d = date.fromisoformat(target_date) if target_date else None
    return reporter.daily_report(d)


@router.get("/equity")
async def equity_curve(request: Request):
    reporter = request.app.state.reporter
    return reporter.equity_curve()


@router.get("/trades")
async def trade_history(request: Request, limit: int = Query(50, ge=1, le=500)):
    reporter = request.app.state.reporter
    return reporter.trade_history(limit)


@router.get("/summary")
async def summary_stats(request: Request):
    reporter = request.app.state.reporter
    return reporter.summary_stats()
