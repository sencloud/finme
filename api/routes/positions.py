"""Positions, account, and orders API routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/positions")
async def get_positions(request: Request):
    ctp = request.app.state.ctp_client
    if not ctp.connected:
        return {"positions": [], "connected": False}
    return {"positions": ctp.get_positions(), "connected": True}


@router.get("/account")
async def get_account(request: Request):
    ctp = request.app.state.ctp_client
    if not ctp.connected:
        return {"account": None, "connected": False}
    return {"account": ctp.get_account(), "connected": True}


@router.get("/orders")
async def get_orders(request: Request, limit: int = Query(50, ge=1, le=500)):
    ctp = request.app.state.ctp_client
    if not ctp.connected:
        return {"orders": [], "connected": False}
    return {"orders": ctp.get_orders(limit), "connected": True}
