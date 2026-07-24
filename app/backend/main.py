import os
import asyncio
import secrets
import logging
import hashlib
import json
import re
from urllib.parse import urlencode, urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import ConnectionPool
import httpx
from dotenv import load_dotenv
from fastapi import (
    FastAPI, Request, Response, HTTPException,
    UploadFile, File, Query, Body,
)
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("equinox")

discord_rate_limited_until: datetime | None = None

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
REDEEM_CODE_PEPPER = os.getenv("REDEEM_CODE_PEPPER")
DEV_ID = 857932717681147954
pending_batches: dict[str, dict] = {}

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL not set")
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            kwargs={"sslmode": "require"},
        )
    return _pool


def get_db():
    return get_pool().connection()


def _close_pool():
    global _pool
    if _pool:
        _pool.close()
        _pool = None


def db_user_is_active(discord_id: str):
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tier, expires_at
                FROM public.user_subscriptions

                WHERE discord_id = %s
                ORDER BY redeemed_at DESC
                LIMIT 1
                """,
                (discord_id,),
            )
            row = cur.fetchone()

    if not row:
        return False, None, None

    tier, expires = row

    
    if expires is not None and getattr(expires, "tzinfo", None) is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if tier == "lifetime" or expires is None:
        return True, tier, None

    return (expires > now), tier, expires


DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8888")




if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
    logger.error("Missing required env: DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI")
else:
    logger.info("Discord OAuth configured for redirect: %s", DISCORD_REDIRECT_URI)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(prune_expired_subs_loop())
    logger.info("Equinox backend started")
    yield
    task.cancel()
    _close_pool()
    logger.info("Equinox backend shut down")


app = FastAPI(lifespan=lifespan)

FRONTEND_ORIGINS = [
    "http://localhost:5500",
    "https://equinoxbot.netlify.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityHeadersMiddleware)


admin_rate_store: dict[str, list[datetime]] = {}


def check_admin_rate(discord_id: str, limit: int = 30, window: int = 60):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window)
    if discord_id not in admin_rate_store:
        admin_rate_store[discord_id] = []
    admin_rate_store[discord_id] = [t for t in admin_rate_store[discord_id] if t > cutoff]
    if len(admin_rate_store[discord_id]) >= limit:
        raise HTTPException(429, "Too many admin requests. Slow down.")
    admin_rate_store[discord_id].append(now)


sessions: dict[str, dict] = {}
used_oauth_states: dict[str, datetime] = {}
used_oauth_codes: dict[str, datetime] = {}

def safe_next(next_url: str | None) -> str:
    if not next_url:
        return FRONTEND_URL

    next_url = next_url.strip()

    if next_url.startswith("/"):
        return FRONTEND_URL.rstrip("/") + next_url

    try:
        u = urlparse(next_url)
        if u.scheme in ("http", "https") and u.netloc in ALLOWED_FRONTEND_HOSTS:
            return next_url
    except Exception:
        pass

    return FRONTEND_URL

discord_rate_limited_until: datetime | None = None


@app.get("/auth/discord/callback")
async def discord_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    
    global discord_rate_limited_until

    if error:
        raise HTTPException(status_code=400, detail=f"Discord OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' parameter")

    now = datetime.now(timezone.utc)

    if discord_rate_limited_until and discord_rate_limited_until > now:
        retry_secs = int((discord_rate_limited_until - now).total_seconds())
        logger.warning("Discord rate limit active for %ds", retry_secs)
        raise HTTPException(
            status_code=429,
            detail={"message": "Server temporarily rate-limited by Discord.", "retry_after": retry_secs},
            headers={"Retry-After": str(retry_secs)},
        )

    expiry_limit = now - timedelta(minutes=10)
    for k in [k for k, v in used_oauth_codes.items() if v < expiry_limit]:
        used_oauth_codes.pop(k, None)
    for k in [k for k, v in used_oauth_states.items() if v < expiry_limit]:
        used_oauth_states.pop(k, None)

    if code in used_oauth_codes:
        logger.info("OAuth code replay detected, redirecting")
        return RedirectResponse(FRONTEND_URL)

    if not state or state in used_oauth_states:
        logger.info("OAuth state invalid or replayed, redirecting")
        return RedirectResponse(FRONTEND_URL)
    
    
    used_oauth_codes[code] = now
    used_oauth_states[state] = now

    next_url = sessions.pop(f"oauth_state:{state}", FRONTEND_URL)
    next_url = safe_next(next_url)

    try:
        async with httpx.AsyncClient() as client:
            logger.info("Exchanging OAuth code for Discord token")
            token_res = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DISCORD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10
            )
        
            if token_res.status_code == 429:
                retry_after_header = token_res.headers.get("Retry-After")
                try:
                    retry_after = int(retry_after_header) if retry_after_header else 60
                except Exception:
                    retry_after = 60
            
                
                discord_rate_limited_until = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
                logger.warning("Discord 429 received, locking for %ds", retry_after)
            
                used_oauth_codes.pop(code, None)
                used_oauth_states.pop(state, None)
            
                raise HTTPException(
                    status_code=429,
                    detail={"message": "Rate limited by Discord. Try again shortly.", "retry_after": retry_after},
                    headers={"Retry-After": str(retry_after)},
                )

            token_res.raise_for_status()
            access_token = token_res.json()["access_token"]

            
            logger.info("Fetching Discord user profile")
            user_res = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            user_res.raise_for_status()
            user = user_res.json()

    except httpx.HTTPError as e:
        used_oauth_codes.pop(code, None)
        used_oauth_states.pop(state, None)
        logger.error("Discord HTTP error: %s", e)
        raise HTTPException(status_code=500, detail="Communication with Discord failed.")

    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.web_sessions (session_id, user_json, csrf_token)
                VALUES (%s, %s, %s)
                """,
                (session_id, json.dumps(user), csrf_token),
            )
        conn.commit()

    response = RedirectResponse(next_url)
    is_prod = DISCORD_REDIRECT_URI.startswith("https://")
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=604800,
    )

    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="lax",
        max_age=604800,
    )

    return response

def make_avatar_url(user: dict) -> str:
    avatar_hash = user.get("avatar")
    user_id = user["id"]
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=128"
    
    discrim = int(user.get("discriminator", "0")) % 5
    return f"https://cdn.discordapp.com/embed/avatars/{discrim}.png"


def get_user_from_session(request: Request) -> dict | None:
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_json FROM public.web_sessions WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()

    if not row:
        return None

    
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    
ALLOWED_FRONTEND_HOSTS = {"equinoxbot.netlify.app"}  

def humanize_remaining(expires_at: datetime, now: datetime) -> str:
    secs = int((expires_at - now).total_seconds())
    if secs <= 0:
        return "expired"

    days = secs // 86400
    years = days // 365
    days %= 365
    months = days // 30
    days %= 30

    parts = []
    if years: parts.append(f"{years} year" + ("s" if years != 1 else ""))
    if months: parts.append(f"{months} month" + ("s" if months != 1 else ""))
    if not parts:
        parts.append(f"{max(1, days)} day" + ("s" if days != 1 else ""))
    return " ".join(parts)


@app.get("/api/premium/{discord_id}")
def api_premium(discord_id: str, request: Request):
    user = get_user_from_session(request)
    api_key = request.headers.get("X-API-Key")
    bot_key = os.getenv("BOT_API_KEY")

    if not user and (not bot_key or api_key != bot_key):
        raise HTTPException(status_code=401, detail="Authentication required")

    active, tier, expires = db_user_is_active(discord_id)
    return {
        "premium": active,
        "tier": tier,
        "expires_at": expires,
    }





@app.get("/api/subscription")
def api_subscription(request: Request):
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    discord_id = int(user["id"])  

    active, tier, expires = db_user_is_active(discord_id)

    started_at = None
    code_used = None

    try:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            with psycopg.connect(db_url, sslmode="require") as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT tier, redeemed_at, expires_at, last_code_hash
                        FROM public.user_subscriptions
                        WHERE discord_id = %s
                        ORDER BY redeemed_at DESC
                        LIMIT 1
                        """,
                        (discord_id,),
                    )
                    row = cur.fetchone()

            if row:
                tier, started_at, expires, last_hash = row
                code_used = f"...{str(last_hash)[-8:]}" if last_hash else None

                now = datetime.now(timezone.utc)

                if tier == "lifetime" or expires is None:
                    active = True
                else:
                    if getattr(expires, "tzinfo", None) is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    active = expires > now

    except Exception as e:
        logger.warning("api_subscription db lookup failed: %s", e)

    return {
        "premium": active,
        "tier": tier,
        "started_at": started_at,
        "expires_at": expires,
        "code_used": code_used,
        "discord_id": str(discord_id),  
    }




@app.post("/auth/logout")
async def logout(request: Request):
    user = get_user_from_session(request)
    response = JSONResponse({"ok": True})
    if user:
        session_id = request.cookies.get("session_id")
        if session_id:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM public.web_sessions WHERE session_id = %s", (session_id,))
                conn.commit()

        response.delete_cookie("session_id", secure=True, samesite="none")
    return response
    
@app.get("/auth/discord/login")
async def discord_login(next: str = "/"):
    
    state = secrets.token_urlsafe(16)

    
    
    sessions[f"oauth_state:{state}"] = next

    
    
    
    
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email",
        "state": state
    }
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{urlencode(params)}")


@app.get("/api/me")
def me(request: Request):
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    
    discord_id = str(user["id"])
    active, tier, expires = db_user_is_active(discord_id)

    return {
        "id": str(discord_id),
        "username": user["username"],
        "discriminator": user["discriminator"],
        "avatar": user["avatar"],
        "avatar_url": make_avatar_url(user),
        "premium": active,
        "tier": tier,
        "expires_at": expires,
        "is_dev": int(discord_id) == DEV_ID,
    }

@app.get("/api/premium")
def api_premium_me(request: Request):
    """
    Premium status for the currently logged-in user (via Discord session).
    Frontend uses this for premium.html and thankyou.html.
    """
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    discord_id = str(user["id"])
    active, tier, expires = db_user_is_active(discord_id)

    return {
        "premium": active,
        "tier": tier,
        "expires_at": expires,
    }

CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$")

def lock_seconds_for_stage(stage: int) -> int:
    base = 30
    secs = base * (2 ** (stage - 1))  
    return min(secs, 3600)

def locked_response(retry_after: int):
    
    raise HTTPException(
        status_code=429,
        detail={
            "message": "Temporarily locked from redeeming. Try again later.",
            "locked": True,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )

@app.post("/api/redeem")
def redeem_code(request: Request, body: dict = Body(...)):
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    discord_id = int(user["id"])  
    now = datetime.now(timezone.utc)

    pepper = os.getenv("REDEEM_CODE_PEPPER")
    if not pepper:
        raise HTTPException(status_code=500, detail="Server misconfigured")

    code_input = (body.get("code") or "").strip()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:

                
                cur.execute(
                    """
                    INSERT INTO public.redeem_attempts (discord_id, fails, lock_until, admin_lock_until, updated_at)
                    VALUES (%s, 0, NULL, NULL, %s)
                    ON CONFLICT (discord_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
                    RETURNING fails, lock_until, admin_lock_until
                    """,
                    (discord_id, now),
                )
                fails, lock_until, admin_lock_until = cur.fetchone()

                
                effective_lock_until = None
                if lock_until and lock_until > now:
                    effective_lock_until = lock_until
                if admin_lock_until and admin_lock_until > now:
                    if effective_lock_until is None or admin_lock_until > effective_lock_until:
                        effective_lock_until = admin_lock_until

                if effective_lock_until:
                    retry_after = int((effective_lock_until - now).total_seconds())
                    conn.commit()
                    locked_response(retry_after)

                def record_fail_and_raise():
                    nonlocal fails
                    fails += 1

                    new_lock_until = None
                    retry_after = None
                    locked_now = False

                    if fails % 3 == 0:
                        stage = fails // 3
                        lock_secs = lock_seconds_for_stage(stage)
                        new_lock_until = now + timedelta(seconds=lock_secs)
                        retry_after = lock_secs
                        locked_now = True

                    cur.execute(
                        """
                        UPDATE public.redeem_attempts
                        SET fails=%s, lock_until=%s, updated_at=%s
                        WHERE discord_id=%s
                        """,
                        (fails, new_lock_until, now, discord_id),
                    )
                    conn.commit()

                    if locked_now:
                        locked_response(retry_after)

                    raise HTTPException(status_code=400, detail="Redeem failed. Please try another code.")

                
                if not code_input:
                    record_fail_and_raise()

                code = code_input

                if "-" not in code:
                    if not re.fullmatch(r"^[A-Za-z0-9]{16}$", code):
                        record_fail_and_raise()
                    code = "-".join([code[i:i+4] for i in range(0, 16, 4)])

                if not CODE_PATTERN.fullmatch(code):
                    record_fail_and_raise()

                code_hash = hashlib.sha256((pepper + code).encode("utf-8")).hexdigest()

                
                cur.execute(
                    """
                    SELECT id, tier, used_at
                    FROM redeem_codes
                    WHERE code_hash = %s
                    FOR UPDATE
                    """,
                    (code_hash,),
                )
                row = cur.fetchone()
                if (not row) or (row[2] is not None):
                    record_fail_and_raise()

                code_id, tier, _used_at = row

                
                cur.execute(
                    """
                    SELECT tier, expires_at
                    FROM public.user_subscriptions

                    WHERE discord_id = %s
                    FOR UPDATE
                    """,
                    (discord_id,),
                )
                sub_row = cur.fetchone()
                current_tier = sub_row[0] if sub_row else None
                current_expires = sub_row[1] if sub_row else None

                if current_expires is not None and getattr(current_expires, "tzinfo", None) is None:
                    current_expires = current_expires.replace(tzinfo=timezone.utc)

                base_time = now
                if current_expires is not None and current_expires > now:
                    base_time = current_expires

                
                if tier == "lifetime" or current_tier == "lifetime":
                    new_tier = "lifetime"
                    new_expires = None
                elif tier == "monthly":
                    new_tier = "monthly"
                    new_expires = base_time + timedelta(days=30)
                elif tier == "yearly":
                    new_tier = "yearly"
                    new_expires = base_time + timedelta(days=365)
                else:
                    raise HTTPException(status_code=500, detail="Invalid tier in DB")

                
                cur.execute(
                    "UPDATE redeem_codes SET used_at=%s, used_by_discord_id=%s WHERE id=%s",
                    (now, discord_id, code_id),
                )

                
                cur.execute(
                    """
                    INSERT INTO redemptions
                    (discord_id, tier, redeemed_at, expires_at, code_hash, code_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (discord_id, tier, now, new_expires, code_hash, code_id),
                )

                
                cur.execute(
                    """
                    INSERT INTO user_subscriptions
                    (discord_id, tier, redeemed_at, expires_at, last_code_hash)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (discord_id) DO UPDATE SET
                      tier = EXCLUDED.tier,
                      redeemed_at = EXCLUDED.redeemed_at,
                      expires_at = EXCLUDED.expires_at,
                      last_code_hash = EXCLUDED.last_code_hash
                    """,
                    (discord_id, new_tier, now, new_expires, code_hash),
                )

                
                cur.execute(
                    """
                    UPDATE public.redeem_attempts
                    SET fails=0, lock_until=NULL, updated_at=%s
                    WHERE discord_id=%s
                    """,
                    (now, discord_id),
                )

            conn.commit()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("REDEEM ERROR")
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"ok": True, "tier": new_tier, "expires_at": new_expires, "discord_id": str(discord_id)}


async def prune_expired_subs_loop():
    while True:
        try:
            now = datetime.now(timezone.utc)
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM public.user_subscriptions

                        WHERE tier != 'lifetime'
                          AND expires_at IS NOT NULL
                          AND expires_at <= %s
                        """,
                        (now,),
                    )
                conn.commit()
            logger.info("Expired subscriptions pruned")
        except Exception as e:
            logger.warning("Prune error: %s", e)

        
        await asyncio.sleep(60 * 60 * 24)

def require_csrf(request: Request):
    session_id = request.cookies.get("session_id")
    csrf_cookie = request.cookies.get("csrf_token")
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(403, "CSRF check failed")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="Missing CSRF token")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT csrf_token FROM public.web_sessions WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()

    if not row or row[0] != csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
        
DEV_DISCORD_ID = "857932717681147954"

def require_dev(request: Request) -> str:
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(401, "Not logged in")
    discord_id = str(user["id"])
    if discord_id != DEV_DISCORD_ID:
        raise HTTPException(403, "Forbidden")
    check_admin_rate(discord_id)
    return discord_id

@app.post("/api/admin/import-codes")
async def admin_import_codes(
    request: Request,
    tier: Literal["monthly", "yearly", "lifetime"] = Query(...),
    file: UploadFile = File(...),
):
    require_dev(request)

    pepper = os.getenv("REDEEM_CODE_PEPPER")
    if not pepper:
        raise HTTPException(status_code=500, detail="Server misconfigured")

    content = (await file.read()).decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]

    inserted = 0
    skipped = 0
    bad = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for raw in lines:
                code = raw.strip()

                
                if "-" not in code:
                    if not re.fullmatch(r"^[A-Za-z0-9]{16}$", code):
                        bad += 1
                        continue
                    code = "-".join([code[i:i+4] for i in range(0, 16, 4)])

                if not CODE_PATTERN.fullmatch(code):
                    bad += 1
                    continue

                code_hash = hashlib.sha256((pepper + code).encode("utf-8")).hexdigest()

                cur.execute(
                    """
                    INSERT INTO redeem_codes (code_hash, tier)
                    VALUES (%s, %s)
                    ON CONFLICT (code_hash) DO NOTHING
                    """,
                    (code_hash, tier),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1

        conn.commit()

    return {"ok": True, "inserted": inserted, "skipped": skipped, "bad": bad}

@app.post("/api/admin/codes/add")
def admin_add_codes(request: Request, body: dict = Body(...)):
    require_dev(request)

    tier = (body.get("tier") or "").lower()
    codes = body.get("codes") or []
    if tier not in ("monthly", "yearly", "lifetime"):
        raise HTTPException(400, "Invalid tier")

    pepper = os.getenv("REDEEM_CODE_PEPPER")
    if not pepper:
        raise HTTPException(status_code=500, detail="Server misconfigured")

    inserted = 0
    skipped = 0
    bad = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for raw in codes:
                code = (raw or "").strip()
                if not code:
                    bad += 1
                    continue

                if "-" not in code:
                    if not re.fullmatch(r"^[A-Za-z0-9]{16}$", code):
                        bad += 1
                        continue
                    code = "-".join([code[i:i+4] for i in range(0, 16, 4)])

                if not CODE_PATTERN.fullmatch(code):
                    bad += 1
                    continue

                h = hashlib.sha256((pepper + code).encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO redeem_codes (code_hash, tier)
                    VALUES (%s, %s)
                    ON CONFLICT (code_hash) DO NOTHING
                    """,
                    (h, tier),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
        conn.commit()

    return {"ok": True, "inserted": inserted, "skipped": skipped, "bad": bad}

@app.post("/api/admin/codes/remove")
def admin_remove_code(request: Request, body: dict = Body(...)):
    require_dev(request)

    raw = (body.get("code") or "").strip()
    if not raw:
        raise HTTPException(400, "Missing code")

    pepper = os.getenv("REDEEM_CODE_PEPPER")
    if not pepper:
        raise HTTPException(status_code=500, detail="Server misconfigured")

    code = raw
    if "-" not in code:
        if not re.fullmatch(r"^[A-Za-z0-9]{16}$", code):
            raise HTTPException(400, "Invalid code format")
        code = "-".join([code[i:i+4] for i in range(0, 16, 4)])

    if not CODE_PATTERN.fullmatch(code):
        raise HTTPException(400, "Invalid code format")

    h = hashlib.sha256((pepper + code).encode("utf-8")).hexdigest()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM redeem_codes WHERE code_hash=%s AND used_at IS NULL", (h,))
            deleted = cur.rowcount
        conn.commit()

    return {"ok": True, "deleted": deleted}

@app.get("/api/admin/premium-users")
def admin_premium_users(request: Request):
    require_dev(request)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT discord_id::text, tier, redeemed_at, expires_at, last_code_hash
                FROM public.user_subscriptions

                ORDER BY redeemed_at DESC
                """
            )
            rows = cur.fetchall()

    def short_hash(h):
        return None if not h else f"...{str(h)[-8:]}"

    return {
        "ok": True,
        "users": [
            {
                "discord_id": str(r[0]),
                "tier": r[1],
                "redeemed_at": r[2],
                "expires_at": r[3],
                "code_used": short_hash(r[4]),
            }
            for r in rows
        ],
    }

@app.post("/api/admin/grant")
def admin_grant(request: Request, body: dict = Body(...)):
    require_dev(request)

    discord_id = str(body.get("discord_id"))
    tier = (body.get("tier") or "").lower()
    code_label = (body.get("code_used") or "MANUAL-GRANT").strip()

    if tier not in ("monthly", "yearly", "lifetime"):
        raise HTTPException(400, "Invalid tier")

    now = datetime.now(timezone.utc)

    if tier == "lifetime":
        expires = None
    elif tier == "monthly":
        expires = now + timedelta(days=30)
    else:
        expires = now + timedelta(days=365)

    
    
    last_code_hash = hashlib.sha256(("ADMIN:" + code_label).encode("utf-8")).hexdigest()

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tier, expires_at FROM public.user_subscriptions WHERE discord_id=%s FOR UPDATE",
                (discord_id,),
            )
            sub = cur.fetchone()
            current_tier = sub[0] if sub else None
            current_expires = sub[1] if sub else None
    
            if current_expires is not None and getattr(current_expires, "tzinfo", None) is None:
                current_expires = current_expires.replace(tzinfo=timezone.utc)
    
            base_time = now
            if current_expires is not None and current_expires > now:
                base_time = current_expires
    
            if tier == "lifetime" or current_tier == "lifetime":
                new_tier = "lifetime"
                new_expires = None
            elif tier == "monthly":
                new_tier = "monthly"
                new_expires = base_time + timedelta(days=30)
            elif tier == "yearly":
                new_tier = "yearly"
                new_expires = base_time + timedelta(days=365)
            else:
                raise HTTPException(400, "Invalid tier")
    
            cur.execute(
                """
                INSERT INTO user_subscriptions (discord_id, tier, redeemed_at, expires_at, last_code_hash)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (discord_id) DO UPDATE SET
                  tier = EXCLUDED.tier,
                  redeemed_at = EXCLUDED.redeemed_at,
                  expires_at = EXCLUDED.expires_at,
                  last_code_hash = EXCLUDED.last_code_hash
                """,
                (discord_id, new_tier, now, new_expires, last_code_hash),
            )
        conn.commit()
    
    return {"ok": True, "discord_id": discord_id, "tier": new_tier, "expires_at": new_expires}

@app.post("/api/admin/reduce")
def admin_reduce(request: Request, body: dict = Body(...)):
    require_dev(request)

    raw = str(body.get("discord_id") or "").strip()
    tier = (body.get("tier") or "").lower().strip()

    if not raw.isdigit():
        raise HTTPException(status_code=400, detail="Invalid Discord ID")
    discord_id = int(raw)

    if tier not in ("monthly", "yearly", "lifetime"):
        raise HTTPException(status_code=400, detail="Invalid tier")

    now = datetime.now(timezone.utc)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tier, expires_at
                FROM public.user_subscriptions

                WHERE discord_id = %s
                FOR UPDATE
                """,
                (discord_id,),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="User has no active subscription.")

            current_tier, current_expires = row

            
            if current_tier == "lifetime" or current_expires is None or tier == "lifetime":
                cur.execute("DELETE FROM public.user_subscriptions WHERE discord_id=%s", (discord_id,))
                conn.commit()
                return {"ok": True, "message": "Reduced lifetime -> revoked subscription."}

            if getattr(current_expires, "tzinfo", None) is None:
                current_expires = current_expires.replace(tzinfo=timezone.utc)

            delta = timedelta(days=30) if tier == "monthly" else timedelta(days=365)

            new_expires = current_expires - delta

            
            if new_expires <= now:
                cur.execute("DELETE FROM public.user_subscriptions WHERE discord_id=%s", (discord_id,))
                conn.commit()
                return {"ok": True, "message": "Reduction exceeded remaining time -> revoked subscription."}

            
            cur.execute(
                """
                UPDATE user_subscriptions
                SET expires_at=%s, redeemed_at=%s
                WHERE discord_id=%s
                """,
                (new_expires, now, discord_id),
            )
        conn.commit()

    return {"ok": True, "message": f"Reduced {tier} from subscription.", "expires_at": new_expires}


@app.post("/api/admin/revoke")
def admin_revoke(request: Request, body: dict = Body(...)):
    require_dev(request)
    discord_id = str(body.get("discord_id"))

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.user_subscriptions WHERE discord_id=%s", (discord_id,))
            deleted = cur.rowcount
        conn.commit()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="That user does not have an active subscription.")

    return {"ok": True, "deleted": deleted}


@app.post("/api/admin/lock")
def admin_lock(request: Request, body: dict = Body(...)):
    require_dev(request)

    discord_id = str(body.get("discord_id"))
    seconds = int(body.get("seconds"))
    if seconds <= 0:
        raise HTTPException(status_code=400, detail="Seconds must be > 0")

    now = datetime.now(timezone.utc)
    admin_lock_until = now + timedelta(seconds=seconds)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.redeem_attempts (discord_id, fails, lock_until, admin_lock_until, updated_at)
                VALUES (%s, 0, NULL, %s, %s)
                ON CONFLICT (discord_id) DO UPDATE SET
                  admin_lock_until = EXCLUDED.admin_lock_until,
                  updated_at = EXCLUDED.updated_at
                RETURNING discord_id, fails, lock_until, admin_lock_until, updated_at
                """,
                (discord_id, admin_lock_until, now),
            )
            row = cur.fetchone()
        conn.commit()

    return {
        "ok": True,
        "discord_id": row[0],
        "fails": row[1],
        "lock_until": row[2],
        "admin_lock_until": row[3],
        "updated_at": row[4],
    }

@app.post("/api/admin/unlock")
def admin_unlock(request: Request, body: dict = Body(...)):
    require_dev(request)
    discord_id = str(body.get("discord_id"))

    now = datetime.now(timezone.utc)
    with get_db() as conn:
        with conn.cursor() as cur:
            
            cur.execute(
                """
                SELECT admin_lock_until
                FROM public.redeem_attempts
                WHERE discord_id = %s
                """,
                (discord_id,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                raise HTTPException(status_code=404, detail="That user is not in admin lockdown.")

            cur.execute(
                """
                UPDATE public.redeem_attempts
                SET admin_lock_until = NULL, updated_at = %s
                WHERE discord_id = %s
                """,
                (now, discord_id),
            )
        conn.commit()

    return {"ok": True}


@app.get("/api/admin/redeem-locks")
def admin_redeem_locks(request: Request):
    require_dev(request)
    now = datetime.now(timezone.utc)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT discord_id::text, fails, lock_until, admin_lock_until, updated_at
                FROM public.redeem_attempts
                WHERE (lock_until IS NOT NULL AND lock_until > %s)
                   OR (admin_lock_until IS NOT NULL AND admin_lock_until > %s)
                ORDER BY GREATEST(
                    COALESCE(lock_until, 'epoch'::timestamptz),
                    COALESCE(admin_lock_until, 'epoch'::timestamptz)
                ) DESC
                """,
                (now, now),
            )
            rows = cur.fetchall()

    locks = []
    for discord_id, fails, lock_until, admin_lock_until, updated_at in rows:
        
        effective = None
        lock_type = None

        if lock_until and lock_until > now:
            effective = lock_until
            lock_type = "bruteforce"

        if admin_lock_until and admin_lock_until > now:
            if effective is None or admin_lock_until > effective:
                effective = admin_lock_until
                lock_type = "admin"

        locks.append({
          "discord_id": str(discord_id),
          "fails": int(fails),
          "lock_until": lock_until,
          "admin_lock_until": admin_lock_until,
          "effective_lock_until": effective,
          "lock_type": lock_type,
          "updated_at": updated_at,
        })

    return {"ok": True, "locks": locks}

CODE_ALPHABET = "qwertyuiopasdfghjklzxcvbnmABCDEFGHJKLMNPQRSTUVWXYZ23456789"  

def generate_code_raw() -> str:
    
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))

def normalize_code(raw: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", (raw or ""))
    if len(raw) != 16:
        raise ValueError("Generated code wrong length")
    return "-".join(raw[i:i+4] for i in range(0, 16, 4))

@app.post("/api/admin/generate-codes")
def admin_generate_codes(request: Request, body: dict = Body(...)):
    require_dev(request)

    tier = (body.get("tier") or "").lower().strip()
    amount = int(body.get("amount") or 0)

    if tier not in ("monthly", "yearly", "lifetime"):
        raise HTTPException(400, detail="Invalid tier")
    if amount <= 0 or amount > 50000:
        raise HTTPException(400, detail="Invalid amount (1..50000)")

    codes = [normalize_code(generate_code_raw()) for _ in range(amount)]

    token = secrets.token_urlsafe(24)
    pending_batches[token] = {
        "tier": tier,
        "codes": codes,
        "created_at": datetime.now(timezone.utc),
    }

    return {
        "ok": True,
        "token": token,
        "tier": tier,
        "amount": amount,
        "codes": codes,  
    }

@app.post("/api/admin/import-generated")
def admin_import_generated(request: Request, body: dict = Body(...)):
    require_dev(request)

    token = (body.get("token") or "").strip()
    if not token or token not in pending_batches:
        raise HTTPException(400, detail="Invalid/expired batch token")

    batch = pending_batches.pop(token)  
    tier = batch["tier"]
    codes = batch["codes"]

    pepper = os.getenv("REDEEM_CODE_PEPPER")
    if not pepper:
        raise HTTPException(500, detail="Server misconfigured")

    inserted = 0
    skipped = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for code in codes:
                code_hash = hashlib.sha256((pepper + code).encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO redeem_codes (code_hash, tier)
                    VALUES (%s, %s)
                    ON CONFLICT (code_hash) DO NOTHING
                    """,
                    (code_hash, tier),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
        conn.commit()

    return {"ok": True, "tier": tier, "inserted": inserted, "skipped": skipped}

@app.post("/api/admin/reject-generated")
def admin_reject_generated(request: Request, body: dict = Body(...)):
    require_dev(request)

    token = (body.get("token") or "").strip()
    if not token or token not in pending_batches:
        raise HTTPException(400, detail="Invalid/expired batch token")

    pending_batches.pop(token, None)
    return {"ok": True}


@app.get("/")
async def root():
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import os, uvicorn
    port = int(os.getenv("SERVER_PORT") or os.getenv("PORT") or "25766")
    uvicorn.run(app, host="0.0.0.0", port=port)

















