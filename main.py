"""
menuboard-image-api
Wraps MiniMax image generation API with rate limiting.
"""
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
MINIMAX_API_HOST = os.getenv("MINIMAX_API_HOST", "https://api.minimaxi.chat")
MINIMAX_ENDPOINT = f"{MINIMAX_API_HOST}/v1/image_generation"

SESSION_LIMIT = 6
SESSION_WINDOW_SECS = 60 * 60  # 1 hour, unused but reserved

# In-memory session tracking: session_id -> list[timestamp]
session_requests: dict[str, list[float]] = defaultdict(list)

# Cleanup threshold
CLEANUP_THRESHOLD = 200
_last_cleanup = time.time()


def _clean_sessions() -> None:
    global _last_cleanup
    now = time.time()
    # Only run cleanup once per 5 minutes
    if len(session_requests) < CLEANUP_THRESHOLD:
        return
    _last_cleanup = now
    cutoff = now - SESSION_WINDOW_SECS
    for sid, timestamps in list(session_requests.items()):
        session_requests[sid] = [ts for ts in timestamps if ts > cutoff]
        if not session_requests[sid]:
            del session_requests[sid]


def _check_rate_limit(session_id: str) -> tuple[int, int]:
    """Returns (remaining, reset_in_secs). Raises HTTPException if limit exceeded."""
    _clean_sessions()
    now = time.time()
    window_cutoff = now - SESSION_WINDOW_SECS
    timestamps = session_requests.get(session_id, [])
    # Only keep timestamps within the window
    recent = [ts for ts in timestamps if ts > window_cutoff]
    session_requests[session_id] = recent

    remaining = SESSION_LIMIT - len(recent)
    if remaining <= 0:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {SESSION_LIMIT} images per session.",
        )
    return remaining - 1, SESSION_WINDOW_SECS


def _record_request(session_id: str) -> None:
    session_requests[session_id].append(time.time())


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4"] = "1:1"


class GenerateResponse(BaseModel):
    image_urls: list[str]
    model: str = "image-01"
    prompt: str
    aspect_ratio: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY environment variable is not set.")
    yield


app = FastAPI(
    title="menuboard-image-api",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    request: Request,
    x_session_id: Annotated[str | None, Header(alias="X-Session-ID")] = None,
) -> GenerateResponse:
    # Use session header, cookie, or IP fallback
    session_id = (
        x_session_id
        or request.cookies.get("session_id")
        or f"ip:{request.client.host}"
    )

    remaining, reset_in = _check_rate_limit(session_id)
    _record_request(session_id)

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "image-01",
        "prompt": body.prompt,
        "aspect_ratio": body.aspect_ratio,
    }

    try:
        resp = requests.post(MINIMAX_ENDPOINT, headers=headers, json=payload, timeout=60)
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="MiniMax API request timed out.")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach MiniMax API: {e}")

    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid response from MiniMax API.")

    if resp.status_code != 200 or data.get("base_resp", {}).get("status_code") != 0:
        status_msg = data.get("base_resp", {}).get("status_msg", "Unknown error")
        raise HTTPException(
            status_code=502,
            detail=f"MiniMax API error: {status_msg}",
        )

    image_urls = data.get("data", {}).get("image_urls", [])
    if not image_urls:
        raise HTTPException(status_code=502, detail="No image returned from MiniMax API.")

    response = JSONResponse(
        content={
            "image_urls": image_urls,
            "model": "image-01",
            "prompt": body.prompt,
            "aspect_ratio": body.aspect_ratio,
        }
    )
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=SESSION_WINDOW_SECS,
        httponly=True,
        same_site="lax",
    )
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)
    return response
