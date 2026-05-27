"""
vk_auth.py — VK OAuth авторизация пользователей KinoVibe.

Поток (implicit flow, без client_secret):
  1. GET /api/vk/auth-url  → редирект на oauth.vk.com
  2. VK редиректит на /vk-callback (фронт) с токеном в URL-хэше
  3. Фронт POST /api/vk/set-token {access_token, user_id}  → сессионная кука
  4. GET /api/vk/status  → {logged_in, user_id, name, photo}
  5. POST /api/vk/logout  → очистить куку

Токен хранится в защищённой HttpOnly куке `vk_session`.
Если кука есть — VK провайдер использует ТОКЕН ПОЛЬЗОВАТЕЛЯ, иначе — глобальный VK_TOKEN.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import json
from typing import Optional

import httpx
from fastapi import APIRouter, Cookie, Response, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("kinovibe.vk_auth")

router = APIRouter(prefix="/vk", tags=["vk-auth"])

VK_APP_ID  = os.getenv("VK_APP_ID", "")
VK_API_VER = "5.131"
_SCOPE     = "video,offline"          # offline = бессрочный токен
_VK_API    = "https://api.vk.com/method"

# In-memory сессии: token_hash → {user_id, name, photo, token, ts}
_SESSIONS: dict[str, dict] = {}
_SESSION_TTL = 3600 * 24 * 30        # 30 дней


# ── helpers ──────────────────────────────────────────────────────────────────

def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def get_user_token(vk_session: Optional[str] = None) -> Optional[str]:
    """Вернуть токен пользователя по сессионной куке (или None)."""
    if not vk_session:
        return None
    sess = _SESSIONS.get(vk_session)
    if not sess:
        return None
    if time.time() - sess["ts"] > _SESSION_TTL:
        _SESSIONS.pop(vk_session, None)
        return None
    return sess["token"]


def get_session_info(vk_session: Optional[str]) -> Optional[dict]:
    if not vk_session:
        return None
    sess = _SESSIONS.get(vk_session)
    if not sess or time.time() - sess["ts"] > _SESSION_TTL:
        return None
    return sess


# ── pydantic ─────────────────────────────────────────────────────────────────

class SetTokenRequest(BaseModel):
    access_token: str
    user_id:      int


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("/auth-url")
def auth_url(redirect_uri: str = ""):
    """Вернуть URL для редиректа на VK OAuth."""
    if not VK_APP_ID:
        raise HTTPException(503, detail="VK_APP_ID не задан в .env — создайте приложение на vk.com/apps")
    if not redirect_uri:
        redirect_uri = "/vk-callback"
    from urllib.parse import urlencode
    params = urlencode({
        "client_id":    VK_APP_ID,
        "redirect_uri": redirect_uri,
        "scope":        _SCOPE,
        "response_type": "token",
        "v":            VK_API_VER,
        "display":      "popup",
    })
    return {"url": f"https://oauth.vk.com/authorize?{params}"}


@router.post("/set-token")
async def set_token(body: SetTokenRequest, response: Response):
    """Принять токен от фронта, получить профиль пользователя, создать сессию."""
    token = body.access_token.strip()
    if not token:
        raise HTTPException(400, "Пустой токен")

    # Проверяем токен через VK API
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{_VK_API}/users.get", params={
                "access_token": token,
                "fields":       "photo_50",
                "v":            VK_API_VER,
            })
        data = r.json()
        if "error" in data:
            raise HTTPException(401, f"VK API: {data['error'].get('error_msg', 'invalid token')}")
        user = data["response"][0]
        name  = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        photo = user.get("photo_50", "")
        uid   = user["id"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Не удалось проверить токен VK: {e}")

    # Сохраняем сессию
    sess_key = _tok_hash(token)
    _SESSIONS[sess_key] = {
        "token":   token,
        "user_id": uid,
        "name":    name,
        "photo":   photo,
        "ts":      time.time(),
    }

    # HttpOnly кука на 30 дней
    response.set_cookie(
        key="vk_session",
        value=sess_key,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,     # поставь True если https
    )
    logger.info(f"[VK Auth] Залогинился: {name} (id={uid})")
    return {"ok": True, "name": name, "photo": photo, "user_id": uid}


@router.get("/status")
def status(vk_session: Optional[str] = Cookie(default=None)):
    """Статус авторизации текущего пользователя."""
    sess = get_session_info(vk_session)
    if not sess:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "user_id":   sess["user_id"],
        "name":      sess["name"],
        "photo":     sess["photo"],
    }


@router.post("/logout")
def logout(response: Response, vk_session: Optional[str] = Cookie(default=None)):
    """Выйти из VK — удалить сессию и куку."""
    if vk_session and vk_session in _SESSIONS:
        name = _SESSIONS[vk_session].get("name", "?")
        _SESSIONS.pop(vk_session)
        logger.info(f"[VK Auth] Вышел: {name}")
    response.delete_cookie("vk_session")
    return {"ok": True}