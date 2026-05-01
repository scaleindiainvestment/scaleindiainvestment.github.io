"""SCALE India Investment — FastAPI backend."""
from __future__ import annotations

import logging
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from auth_utils import (
    USERNAME_RE,
    create_token,
    decode_token,
    get_current_user,
    hash_password,
    rate_limit,
    require_admin,
    verify_password,
)
from data.stocks import STOCKS, STOCK_MAP
from emailer import (
    fire_and_forget,
    make_action_token,
    notify_admin_new_application,
    notify_user_approved,
    notify_user_rejected,
    verify_action_token,
)
from market_timing import session_info
from nse_fetcher import fetcher

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("server")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

APPROVAL_DAYS = 10
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

# Collections
users_col = db["users"]
portfolios_col = db["portfolios"]
holdings_col = db["holdings"]
transactions_col = db["transactions"]
watchlists_col = db["watchlists"]

FAKE_USERS = [
    "TraderRaj", "BullRunKing", "NSEWatcher", "DalalStreet", "OptionSamurai",
    "CircuitBreaker", "MumbaiMoneyMan", "NiftyNinja", "SensexSiddha", "PaperHandsPro",
    "BlockDealer", "F_O_Fury", "ChartGuru", "SmallCapSultan", "DividendDon",
]


# --------------------- Lifespan ---------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ensure unique username index (case-insensitive)
    try:
        await users_col.create_index(
            "username_lc", unique=True, name="uniq_username_lc"
        )
        await portfolios_col.create_index("userId", unique=True)
        await holdings_col.create_index([("userId", 1), ("symbol", 1)], unique=True)
        await watchlists_col.create_index([("userId", 1), ("symbol", 1)], unique=True)
    except Exception as e:
        logger.warning("Index creation warn: %s", e)
    # seed fake users (first boot)
    await _seed_fake_users()
    # seed admin
    await _seed_admin()
    # grandfather existing users without approval fields
    await _migrate_grandfather()
    # start fetcher
    fetcher.start()
    yield
    await fetcher.stop()
    mongo_client.close()


app = FastAPI(lifespan=lifespan)
api = APIRouter(prefix="/api")


# --------------------- Models ---------------------
class SignupReq(BaseModel):
    username: str
    password: str
    email: str
    reason: str = ""  # optional — kept for backward compatibility


class LoginReq(BaseModel):
    username: str
    password: str


class ReapplyReq(BaseModel):
    username: str
    password: str
    reason: str


class AdminActionReq(BaseModel):
    userId: str


class AdminLeaderboardVisibilityReq(BaseModel):
    userId: str
    hidden: bool


class ChangePasswordReq(BaseModel):
    currentPassword: str
    newPassword: str


class TradeReq(BaseModel):
    symbol: str
    type: str  # BUY | SELL
    qty: int
    price: float


class WatchlistReq(BaseModel):
    symbol: str


# --------------------- Helpers ---------------------
def _portfolio_doc(user_id: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "userId": user_id,
        "cash": 100000.0,
        "resetCount": 0,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


async def _seed_fake_users():
    existing = await portfolios_col.count_documents({"fake": True})
    if existing >= len(FAKE_USERS):
        return
    for name in FAKE_USERS:
        lc = name.lower()
        if await portfolios_col.find_one({"fake": True, "username_lc": lc}):
            continue
        # random portfolio
        cash = random.uniform(10000, 90000)
        invested_value = random.uniform(20000, 80000)
        current_value = invested_value * random.uniform(0.78, 1.42)
        await portfolios_col.insert_one({
            "id": str(uuid.uuid4()),
            "userId": f"fake_{lc}",
            "username": name,
            "username_lc": lc,
            "fake": True,
            "cash": round(cash, 2),
            "invested": round(invested_value, 2),
            "currentValue": round(cash + current_value, 2),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
    logger.info("Seeded %d fake leaderboard users", len(FAKE_USERS))


async def _seed_admin():
    """Seed the admin account on first boot if missing."""
    lc = ADMIN_USERNAME.lower()
    existing = await users_col.find_one({"username_lc": lc})
    if existing:
        # ensure isAdmin + approved (idempotent)
        await users_col.update_one(
            {"username_lc": lc},
            {"$set": {"isAdmin": True, "status": "approved"}},
        )
        return
    user_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    await users_col.insert_one({
        "id": user_id,
        "username": ADMIN_USERNAME,
        "username_lc": lc,
        "passwordHash": hash_password(ADMIN_PASSWORD),
        "email": "admin@scale.local",
        "reason": "Creator account",
        "status": "approved",
        "isAdmin": True,
        "approvedAt": now_iso,
        "approvedUntil": None,  # never expires for admin
        "createdAt": now_iso,
    })
    await portfolios_col.insert_one(_portfolio_doc(user_id))
    logger.info("Seeded admin user '%s'", ADMIN_USERNAME)


async def _migrate_grandfather():
    """Existing users without status field → grandfathered approved with 10-day expiry from createdAt."""
    cursor = users_col.find({"status": {"$exists": False}}, {"_id": 0})
    n = 0
    async for u in cursor:
        try:
            created = datetime.fromisoformat(u["createdAt"]) if u.get("createdAt") else datetime.now(timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            created = datetime.now(timezone.utc)
        approved_until = created + timedelta(days=APPROVAL_DAYS)
        await users_col.update_one(
            {"id": u["id"]},
            {"$set": {
                "status": "approved",
                "isAdmin": False,
                "approvedAt": created.isoformat(),
                "approvedUntil": approved_until.isoformat(),
                "email": u.get("email", ""),
                "reason": u.get("reason", "Grandfathered user"),
            }},
        )
        n += 1
    if n:
        logger.info("Grandfathered %d existing users (10-day expiry from signup)", n)


def _is_user_active(user: dict) -> tuple[bool, str]:
    """Return (allowed, reason). Admin always allowed."""
    if user.get("isAdmin"):
        return True, ""
    status = user.get("status", "pending")
    if status == "pending":
        return False, "Your account is pending creator approval. You'll be notified once approved."
    if status == "rejected":
        return False, "Your application was not approved. You may submit a new application via the Apply tab."
    # approved → check expiry
    exp_iso = user.get("approvedUntil")
    if exp_iso:
        try:
            exp = datetime.fromisoformat(exp_iso)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                return False, "Your access has expired (10 days). Submit a new application via the Apply tab."
        except Exception:
            pass
    return True, ""


async def _compute_portfolio(user_id: str) -> dict:
    port = await portfolios_col.find_one({"userId": user_id}, {"_id": 0})
    if not port:
        port = _portfolio_doc(user_id)
        await portfolios_col.insert_one({**port})
    holdings = await holdings_col.find({"userId": user_id}, {"_id": 0}).to_list(500)
    prices = fetcher.cache.all()
    invested = 0.0
    current = 0.0
    day_change = 0.0
    enriched = []
    for h in holdings:
        pdata = prices.get(h["symbol"], {}) or {}
        ltp = pdata.get("price") or h["avgBuyPrice"]
        prev_close = pdata.get("close") or ltp
        curr_val = ltp * h["qty"]
        inv_val = h["avgBuyPrice"] * h["qty"]
        pnl = curr_val - inv_val
        pnl_pct = (pnl / inv_val * 100) if inv_val > 0 else 0
        day_change += (ltp - prev_close) * h["qty"]
        invested += inv_val
        current += curr_val
        enriched.append({
            **h,
            "ltp": round(ltp, 2),
            "currentValue": round(curr_val, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "change": round(ltp - prev_close, 2),
        })
    total_value = port["cash"] + current
    total_pnl = total_value - 100000
    total_pnl_pct = (total_pnl / 100000) * 100
    return {
        "cash": round(port["cash"], 2),
        "holdings": enriched,
        "totalInvested": round(invested, 2),
        "holdingsValue": round(current, 2),
        "totalValue": round(total_value, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "dayChange": round(day_change, 2),
        "resetCount": port.get("resetCount", 0),
    }


# --------------------- Auth ---------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@api.post("/auth/signup")
async def signup(body: SignupReq, request: Request):
    rate_limit(f"signup:{request.client.host}", max_calls=10, window_sec=60)
    username = body.username.strip()
    email = (body.email or "").strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username must be 3–20 chars (letters, numbers, underscore).")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please provide a valid email address.")
    lc = username.lower()
    existing = await users_col.find_one({"username_lc": lc})
    now_iso = datetime.now(timezone.utc).isoformat()

    # Allow re-application from the same Apply form when the user was rejected
    # or their approval window expired.
    if existing:
        if existing.get("isAdmin"):
            raise HTTPException(409, "Username already taken")
        status = existing.get("status", "pending")
        exp_iso = existing.get("approvedUntil")
        expired = False
        if status == "approved" and exp_iso:
            try:
                exp = datetime.fromisoformat(exp_iso)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                expired = datetime.now(timezone.utc) > exp
            except Exception:
                expired = False
        can_reapply = status == "rejected" or expired
        if not can_reapply:
            raise HTTPException(409, "Username already taken")
        # Reset account to pending with new credentials
        await users_col.update_one(
            {"id": existing["id"]},
            {"$set": {
                "passwordHash": hash_password(body.password),
                "email": email,
                "status": "pending",
                "approvedAt": None,
                "approvedUntil": None,
                "reappliedAt": now_iso,
            }},
        )
        fire_and_forget(notify_admin_new_application(existing["username"], email, "", existing["id"]))
        return {
            "submitted": True,
            "message": "New application submitted. You'll be able to log in once the creator approves your access.",
        }

    user_id = str(uuid.uuid4())
    await users_col.insert_one({
        "id": user_id,
        "username": username,
        "username_lc": lc,
        "passwordHash": hash_password(body.password),
        "email": email,
        "reason": "",
        "status": "pending",
        "isAdmin": False,
        "approvedAt": None,
        "approvedUntil": None,
        "createdAt": now_iso,
    })
    await portfolios_col.insert_one(_portfolio_doc(user_id))
    fire_and_forget(notify_admin_new_application(username, email, "", user_id))
    return {
        "submitted": True,
        "message": "Application submitted. You'll be able to log in once the creator approves your access.",
    }


@api.post("/auth/login")
async def login(body: LoginReq, request: Request):
    rate_limit(f"login:{request.client.host}", max_calls=10, window_sec=60)
    lc = body.username.strip().lower()
    user = await users_col.find_one({"username_lc": lc})
    if not user or not verify_password(body.password, user["passwordHash"]):
        raise HTTPException(401, "Invalid username or password")
    ok, reason = _is_user_active(user)
    if not ok:
        # 403 with status indicator so UI can offer reapply
        raise HTTPException(status_code=403, detail=reason)
    token = create_token(user["id"], user["username"], bool(user.get("isAdmin", False)))
    return {
        "token": token,
        "userId": user["id"],
        "username": user["username"],
        "isAdmin": bool(user.get("isAdmin", False)),
    }


@api.post("/auth/reapply")
async def reapply(body: ReapplyReq, request: Request):
    """User submits a new application after rejection or 10-day expiry."""
    rate_limit(f"reapply:{request.client.host}", max_calls=5, window_sec=60)
    lc = body.username.strip().lower()
    user = await users_col.find_one({"username_lc": lc})
    if not user or not verify_password(body.password, user["passwordHash"]):
        raise HTTPException(401, "Invalid username or password")
    if user.get("isAdmin"):
        raise HTTPException(400, "Admin doesn't need to reapply.")
    if len((body.reason or "").strip()) < 10:
        raise HTTPException(400, "Please tell us why you'd like access (min 10 characters).")
    await users_col.update_one(
        {"id": user["id"]},
        {"$set": {
            "status": "pending",
            "reason": body.reason.strip(),
            "approvedAt": None,
            "approvedUntil": None,
            "reappliedAt": datetime.now(timezone.utc).isoformat(),
        }},
    )
    fire_and_forget(notify_admin_new_application(user["username"], user.get("email", ""), body.reason.strip(), user["id"]))
    return {"submitted": True, "message": "New application submitted. Awaiting creator approval."}


@api.get("/auth/me")
async def me(user=Depends(get_current_user)):
    # Re-fetch full user to enforce live access state
    u = await users_col.find_one({"id": user["userId"]}, {"_id": 0})
    if not u:
        raise HTTPException(401, "User not found")
    ok, reason = _is_user_active(u)
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    return {
        "userId": u["id"],
        "username": u["username"],
        "isAdmin": bool(u.get("isAdmin", False)),
        "approvedUntil": u.get("approvedUntil"),
    }


# --------------------- Stocks / Prices / Market ---------------------
@api.get("/stocks")
async def list_stocks():
    return {"stocks": STOCKS}


@api.get("/prices")
async def all_prices():
    return {
        "prices": fetcher.cache.all(),
        "sparks": {s: fetcher.cache.spark_data(s) for s in STOCK_MAP},
        "lastFullUpdate": fetcher.cache.last_full_update,
    }


@api.get("/indices")
async def indices():
    return {"indices": fetcher.cache.indices, "lastFetchedAt": _ist_now_str_local()}


def _ist_now_str_local() -> str:
    from datetime import datetime as _dt
    import pytz as _p
    return _dt.now(_p.timezone("Asia/Kolkata")).strftime("%d-%b-%Y %H:%M:%S")


@api.get("/market-status")
async def market_status():
    # Prefer cached status which includes NSE's real open/close (handles holidays)
    status = fetcher.cache.market_status or session_info()
    return status


# --------------------- Portfolio / Trade ---------------------
@api.get("/portfolio")
async def get_portfolio(user=Depends(get_current_user)):
    return await _compute_portfolio(user["userId"])


@api.post("/trade")
async def trade(body: TradeReq, user=Depends(get_current_user)):
    user_id = user["userId"]
    symbol = body.symbol
    if symbol not in STOCK_MAP:
        raise HTTPException(400, "Unknown symbol")
    if body.qty <= 0:
        raise HTTPException(400, "Quantity must be positive")
    # Block trading when NSE is closed (holiday / outside hours)
    ms = fetcher.cache.market_status or session_info()
    if (ms.get("status") or "").upper() != "OPEN":
        raise HTTPException(400, "Trading is disabled. NSE is currently closed.")
    # always use latest cache price to prevent stale trades
    cached = fetcher.cache.get(symbol) or {}
    live_price = cached.get("price") or body.price
    if not live_price or live_price <= 0:
        raise HTTPException(400, "Price unavailable. Try again in a moment.")
    total = live_price * body.qty
    meta = STOCK_MAP[symbol]
    port = await portfolios_col.find_one({"userId": user_id})
    if not port:
        port = _portfolio_doc(user_id)
        await portfolios_col.insert_one({**port})

    if body.type == "BUY":
        if port["cash"] < total:
            raise HTTPException(400, "Insufficient cash")
        new_cash = port["cash"] - total
        existing = await holdings_col.find_one({"userId": user_id, "symbol": symbol})
        if existing:
            new_qty = existing["qty"] + body.qty
            new_avg = (existing["avgBuyPrice"] * existing["qty"] + total) / new_qty
            await holdings_col.update_one(
                {"userId": user_id, "symbol": symbol},
                {"$set": {"qty": new_qty, "avgBuyPrice": round(new_avg, 4)}},
            )
        else:
            await holdings_col.insert_one({
                "id": str(uuid.uuid4()),
                "userId": user_id,
                "symbol": symbol,
                "name": meta["name"],
                "sector": meta["sector"],
                "qty": body.qty,
                "avgBuyPrice": round(live_price, 4),
            })
        await portfolios_col.update_one({"userId": user_id}, {"$set": {"cash": new_cash}})
    elif body.type == "SELL":
        existing = await holdings_col.find_one({"userId": user_id, "symbol": symbol})
        if not existing or existing["qty"] < body.qty:
            raise HTTPException(400, "Not enough shares to sell")
        new_qty = existing["qty"] - body.qty
        if new_qty == 0:
            await holdings_col.delete_one({"userId": user_id, "symbol": symbol})
        else:
            await holdings_col.update_one(
                {"userId": user_id, "symbol": symbol},
                {"$set": {"qty": new_qty}},
            )
        new_cash = port["cash"] + total
        await portfolios_col.update_one({"userId": user_id}, {"$set": {"cash": new_cash}})
    else:
        raise HTTPException(400, "Invalid trade type")

    await transactions_col.insert_one({
        "id": str(uuid.uuid4()),
        "userId": user_id,
        "symbol": symbol,
        "name": meta["name"],
        "sector": meta["sector"],
        "type": body.type,
        "qty": body.qty,
        "price": round(live_price, 4),
        "total": round(total, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    portfolio = await _compute_portfolio(user_id)
    return {"ok": True, "fillPrice": round(live_price, 4), "portfolio": portfolio}


@api.get("/transactions")
async def get_transactions(user=Depends(get_current_user)):
    rows = (
        await transactions_col.find({"userId": user["userId"]}, {"_id": 0})
        .sort("timestamp", -1)
        .to_list(100)
    )
    return {"transactions": rows}


# --------------------- Watchlist ---------------------
@api.get("/watchlist")
async def get_watchlist(user=Depends(get_current_user)):
    rows = (
        await watchlists_col.find({"userId": user["userId"]}, {"_id": 0})
        .sort("addedAt", -1)
        .to_list(500)
    )
    return {"symbols": [r["symbol"] for r in rows if r["symbol"] in STOCK_MAP]}


@api.post("/watchlist")
async def add_watchlist(body: WatchlistReq, user=Depends(get_current_user)):
    sym = body.symbol.upper().strip()
    if sym not in STOCK_MAP:
        raise HTTPException(400, "Unknown symbol")
    await watchlists_col.update_one(
        {"userId": user["userId"], "symbol": sym},
        {"$setOnInsert": {
            "userId": user["userId"],
            "symbol": sym,
            "addedAt": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"ok": True, "symbol": sym}


@api.delete("/watchlist/{symbol}")
async def remove_watchlist(symbol: str, user=Depends(get_current_user)):
    sym = symbol.upper().strip()
    await watchlists_col.delete_one({"userId": user["userId"], "symbol": sym})
    return {"ok": True, "symbol": sym}


# --------------------- Leaderboard ---------------------
@api.get("/leaderboard")
async def leaderboard(user=Depends(get_current_user)):
    prices = fetcher.cache.all()

    # real users
    real_rows = []
    async for port in portfolios_col.find({"fake": {"$ne": True}}, {"_id": 0}):
        uid = port["userId"]
        u = await users_col.find_one({"id": uid}, {"_id": 0, "username": 1, "leaderboardHidden": 1, "isAdmin": 1})
        if not u:
            continue
        # Hidden users (admin-set) and admin accounts are excluded from public leaderboard,
        # except the current viewer always sees their own row.
        is_self = uid == user["userId"]
        if not is_self:
            if u.get("leaderboardHidden"):
                continue
            if u.get("isAdmin"):
                continue
        holdings = await holdings_col.find({"userId": uid}, {"_id": 0}).to_list(500)
        current_val = port.get("cash", 0)
        for h in holdings:
            p = prices.get(h["symbol"], {}).get("price") or h["avgBuyPrice"]
            current_val += p * h["qty"]
        ret_pct = (current_val / 100000 - 1) * 100
        real_rows.append({
            "username": u["username"],
            "currentValue": round(current_val, 2),
            "pnl": round(current_val - 100000, 2),
            "returnPct": round(ret_pct, 2),
            "isCurrentUser": uid == user["userId"],
            "fake": False,
        })

    # fake users (always enabled, limit to fill up to 10 combined)
    fake_rows = []
    async for port in portfolios_col.find({"fake": True}, {"_id": 0}):
        cv = port.get("currentValue", 100000)
        ret_pct = (cv / 100000 - 1) * 100
        fake_rows.append({
            "username": port["username"],
            "currentValue": round(cv, 2),
            "pnl": round(cv - 100000, 2),
            "returnPct": round(ret_pct, 2),
            "isCurrentUser": False,
            "fake": True,
        })

    combined = real_rows + fake_rows
    combined.sort(key=lambda r: r["returnPct"], reverse=True)

    # rank assignment
    for i, r in enumerate(combined, start=1):
        r["rank"] = i
        r["badge"] = _badge_for(r["returnPct"])

    top = combined[:20]
    my_row = next((r for r in combined if r["isCurrentUser"]), None)
    if my_row and my_row not in top:
        top.append(my_row)

    return {
        "rows": top,
        "totalPlayers": len(combined),
        "me": my_row,
    }


def _badge_for(pct: float) -> str:
    if pct > 20:
        return "BULL"
    if pct >= 10:
        return "TRADER"
    if pct >= 0:
        return "HODLER"
    return "BEAR"


# --------------------- Admin (creator-only) ---------------------
def _user_app_view(u: dict) -> dict:
    return {
        "userId": u["id"],
        "username": u["username"],
        "email": u.get("email", ""),
        "reason": u.get("reason", ""),
        "status": u.get("status", "pending"),
        "isAdmin": bool(u.get("isAdmin", False)),
        "createdAt": u.get("createdAt"),
        "approvedAt": u.get("approvedAt"),
        "approvedUntil": u.get("approvedUntil"),
        "reappliedAt": u.get("reappliedAt"),
        "leaderboardHidden": bool(u.get("leaderboardHidden", False)),
    }


@api.get("/admin/applications")
async def admin_list(admin=Depends(require_admin)):
    rows = []
    async for u in users_col.find({"isAdmin": {"$ne": True}}, {"_id": 0, "passwordHash": 0}):
        rows.append(_user_app_view(u))
    # Newest first within each status (sort by reappliedAt || createdAt DESC)
    rows.sort(key=lambda r: r.get("reappliedAt") or r.get("createdAt") or "", reverse=True)
    pending = [r for r in rows if r["status"] == "pending"]
    approved = [r for r in rows if r["status"] == "approved"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "counts": {"pending": len(pending), "approved": len(approved), "rejected": len(rejected)},
    }


@api.post("/admin/approve")
async def admin_approve(body: AdminActionReq, admin=Depends(require_admin)):
    user = await users_col.find_one({"id": body.userId})
    if not user:
        raise HTTPException(404, "User not found")
    if user.get("isAdmin"):
        raise HTTPException(400, "Cannot modify admin account")
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=APPROVAL_DAYS)
    await users_col.update_one(
        {"id": body.userId},
        {"$set": {
            "status": "approved",
            "approvedAt": now.isoformat(),
            "approvedUntil": until.isoformat(),
        }},
    )
    fire_and_forget(notify_user_approved(user.get("email", ""), user["username"], until.isoformat()))
    return {"ok": True, "approvedUntil": until.isoformat()}


@api.post("/admin/reject")
async def admin_reject(body: AdminActionReq, admin=Depends(require_admin)):
    user = await users_col.find_one({"id": body.userId})
    if not user:
        raise HTTPException(404, "User not found")
    if user.get("isAdmin"):
        raise HTTPException(400, "Cannot modify admin account")
    await users_col.update_one(
        {"id": body.userId},
        {"$set": {
            "status": "rejected",
            "approvedAt": None,
            "approvedUntil": None,
        }},
    )
    fire_and_forget(notify_user_rejected(user.get("email", ""), user["username"]))
    return {"ok": True}


@api.post("/admin/revoke")
async def admin_revoke(body: AdminActionReq, admin=Depends(require_admin)):
    """Revoke an approved user back to pending."""
    user = await users_col.find_one({"id": body.userId})
    if not user:
        raise HTTPException(404, "User not found")
    if user.get("isAdmin"):
        raise HTTPException(400, "Cannot modify admin account")
    await users_col.update_one(
        {"id": body.userId},
        {"$set": {
            "status": "pending",
            "approvedAt": None,
            "approvedUntil": None,
        }},
    )
    return {"ok": True}


@api.post("/admin/leaderboard-visibility")
async def admin_leaderboard_visibility(body: AdminLeaderboardVisibilityReq, admin=Depends(require_admin)):
    """Hide/show a user from the public leaderboard."""
    user = await users_col.find_one({"id": body.userId})
    if not user:
        raise HTTPException(404, "User not found")
    if user.get("isAdmin"):
        raise HTTPException(400, "Cannot modify admin account")
    await users_col.update_one(
        {"id": body.userId},
        {"$set": {"leaderboardHidden": bool(body.hidden)}},
    )
    return {"ok": True, "leaderboardHidden": bool(body.hidden)}


@api.post("/admin/bulk/reject-pending")
async def admin_bulk_reject_pending(admin=Depends(require_admin)):
    """Reject every user currently in 'pending' status (excludes admins)."""
    targets = await users_col.find(
        {"status": "pending", "isAdmin": {"$ne": True}},
        {"_id": 0, "id": 1, "email": 1, "username": 1},
    ).to_list(10000)
    if not targets:
        return {"ok": True, "count": 0}
    ids = [t["id"] for t in targets]
    await users_col.update_many(
        {"id": {"$in": ids}},
        {"$set": {"status": "rejected", "approvedAt": None, "approvedUntil": None}},
    )
    for t in targets:
        if t.get("email"):
            fire_and_forget(notify_user_rejected(t["email"], t["username"]))
    return {"ok": True, "count": len(ids)}


@api.post("/admin/bulk/revoke-approved")
async def admin_bulk_revoke_approved(admin=Depends(require_admin)):
    """Revoke every approved user back to pending (excludes admins)."""
    res = await users_col.update_many(
        {"status": "approved", "isAdmin": {"$ne": True}},
        {"$set": {"status": "pending", "approvedAt": None, "approvedUntil": None}},
    )
    return {"ok": True, "count": res.modified_count}


@api.post("/admin/bulk/leaderboard-hide-all")
async def admin_bulk_leaderboard_hide_all(admin=Depends(require_admin)):
    """Hide every non-admin user from the public leaderboard."""
    res = await users_col.update_many(
        {"isAdmin": {"$ne": True}},
        {"$set": {"leaderboardHidden": True}},
    )
    return {"ok": True, "count": res.modified_count}


@api.post("/admin/bulk/leaderboard-show-all")
async def admin_bulk_leaderboard_show_all(admin=Depends(require_admin)):
    """Make every non-admin user visible on the public leaderboard."""
    res = await users_col.update_many(
        {"isAdmin": {"$ne": True}},
        {"$set": {"leaderboardHidden": False}},
    )
    return {"ok": True, "count": res.modified_count}


# --------------------- Change Password ---------------------
@api.post("/auth/change-password")
async def change_password(body: ChangePasswordReq, user=Depends(get_current_user)):
    if len(body.newPassword) < 6:
        raise HTTPException(400, "New password must be at least 6 characters.")
    if body.newPassword == body.currentPassword:
        raise HTTPException(400, "New password must differ from current password.")
    u = await users_col.find_one({"id": user["userId"]})
    if not u or not verify_password(body.currentPassword, u["passwordHash"]):
        raise HTTPException(401, "Current password is incorrect.")
    await users_col.update_one(
        {"id": user["userId"]},
        {"$set": {"passwordHash": hash_password(body.newPassword)}},
    )
    return {"ok": True, "message": "Password updated successfully."}


# --------------------- Intraday Chart Data (Yahoo Finance) ---------------------
@api.get("/chart/{symbol}")
async def chart(symbol: str, range: str = "1d", interval: str | None = None, user=Depends(get_current_user)):
    """Return OHLC candles from Yahoo Finance for the given NSE symbol.
    range: 1d|5d|1mo|3mo|6mo|1y
    interval auto-picked from range if not supplied.
    """
    if symbol not in STOCK_MAP:
        raise HTTPException(404, "Unknown symbol")
    rng = range.lower()
    intv_map = {"1d": "5m", "5d": "30m", "1mo": "1d", "3mo": "1d", "6mo": "1d", "1y": "1d"}
    if rng not in intv_map:
        raise HTTPException(400, "Unsupported range")
    intv = interval or intv_map[rng]
    y_sym = symbol.replace("&", "%26")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{y_sym}.NS?interval={intv}&range={rng}"
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code != 200:
            raise HTTPException(502, f"Yahoo returned {r.status_code}")
        j = r.json()
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            raise HTTPException(502, "No chart data")
        result = res[0]
        ts = result.get("timestamp") or []
        ind = (result.get("indicators") or {}).get("quote") or [{}]
        q = ind[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
        meta = result.get("meta") or {}
        candles = []
        for i, t in enumerate(ts):
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            v = vols[i] if i < len(vols) else None
            if None in (o, h, l, c):
                continue
            candles.append({
                "t": int(t) * 1000,
                "o": round(float(o), 2),
                "h": round(float(h), 2),
                "l": round(float(l), 2),
                "c": round(float(c), 2),
                "v": int(v or 0),
            })
        return {
            "symbol": symbol,
            "range": rng,
            "interval": intv,
            "currency": meta.get("currency", "INR"),
            "previousClose": meta.get("chartPreviousClose"),
            "regularMarketPrice": meta.get("regularMarketPrice"),
            "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
            "candles": candles,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Chart fetch failed: %s", e)
        raise HTTPException(502, "Chart data unavailable, try again shortly.")


# --------------------- One-click Admin action (from email) ---------------------
def _action_html(title: str, message: str, color: str = "#00d4aa") -> str:
    url = os.environ.get("PUBLIC_APP_URL", "")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>SCALE · {title}</title>
<style>
body{{background:#0a0e1a;color:#e2e8f0;font-family:Inter,Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:14px;padding:36px;max-width:480px;width:100%;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,.6);}}
h1{{color:{color};font-size:22px;margin:0 0 10px;}}
p{{color:#94a3b8;font-size:14px;line-height:1.5;}}
.brand{{font-family:'JetBrains Mono',monospace;color:#a01e20;font-size:22px;font-weight:700;margin-bottom:18px;letter-spacing:-0.02em;}}
.sub{{color:#94a3b8;font-size:11px;letter-spacing:0.18em;text-transform:uppercase;margin-bottom:22px;}}
a.btn{{display:inline-block;margin-top:18px;background:#a01e20;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600;}}
</style></head><body>
<div class="card">
<div class="brand">SCALE</div>
<div class="sub">India Investment</div>
<h1>{title}</h1>
<p>{message}</p>
<a class="btn" href="{url}">Open SCALE →</a>
</div></body></html>"""


@app.get("/api/admin/action")
async def admin_action_one_click(token: str = Query(...)):
    """One-click approve/reject from email. Returns an HTML page."""
    from fastapi.responses import HTMLResponse
    try:
        data = verify_action_token(token)
    except Exception:
        return HTMLResponse(_action_html("Link invalid or expired", "This approval link is no longer valid. Please use the Admin tab.", "#f03e3e"), status_code=400)
    user_id = data["uid"]
    action = data["act"]
    user = await users_col.find_one({"id": user_id})
    if not user:
        return HTMLResponse(_action_html("User not found", "This user no longer exists.", "#f03e3e"), status_code=404)
    if user.get("isAdmin"):
        return HTMLResponse(_action_html("Not allowed", "You cannot modify the admin account.", "#f03e3e"), status_code=400)
    # If already handled, show idempotent success
    current_status = user.get("status")
    if action == "approve":
        if current_status == "approved":
            return HTMLResponse(_action_html("Already approved", f"@{user['username']} is already approved.", "#00d4aa"))
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=APPROVAL_DAYS)
        await users_col.update_one(
            {"id": user_id},
            {"$set": {"status": "approved", "approvedAt": now.isoformat(), "approvedUntil": until.isoformat()}},
        )
        fire_and_forget(notify_user_approved(user.get("email", ""), user["username"], until.isoformat()))
        return HTMLResponse(_action_html(
            "Approved ✓",
            f"@{user['username']} now has 10-day access. Email sent.",
            "#00d4aa",
        ))
    elif action == "reject":
        if current_status == "rejected":
            return HTMLResponse(_action_html("Already rejected", f"@{user['username']} was already rejected.", "#f03e3e"))
        await users_col.update_one(
            {"id": user_id},
            {"$set": {"status": "rejected", "approvedAt": None, "approvedUntil": None}},
        )
        fire_and_forget(notify_user_rejected(user.get("email", ""), user["username"]))
        return HTMLResponse(_action_html("Rejected", f"@{user['username']} has been rejected. Email sent.", "#f03e3e"))
    return HTMLResponse(_action_html("Unknown action", "Invalid action in link.", "#f03e3e"), status_code=400)


# --------------------- WebSocket ---------------------
@app.websocket("/api/ws")
async def ws_endpoint(ws: WebSocket, token: Optional[str] = Query(None)):
    if not token:
        await ws.close(code=4401)
        return
    try:
        decode_token(token)
    except HTTPException:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = fetcher.subscribe()
    # initial snapshot
    try:
        await ws.send_json({"type": "PRICES", "data": fetcher.cache.all()})
        if fetcher.cache.market_status:
            await ws.send_json({"type": "MARKET_STATUS", "data": fetcher.cache.market_status})
        if fetcher.cache.indices:
            await ws.send_json({"type": "INDICES", "data": fetcher.cache.indices})
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS error: %s", e)
    finally:
        fetcher.unsubscribe(q)


# --------------------- Boilerplate ---------------------
@api.get("/")
async def root():
    return {"app": "SCALE India Investment", "status": "ok"}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
