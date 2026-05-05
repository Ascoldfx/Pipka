"""Google OAuth2 authentication routes."""
from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.database import async_session
from app.services.user_service import get_or_create_google_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/auth/google/login")
async def google_login(request: Request):
    """Redirect user to Google OAuth consent screen."""
    redirect_uri = str(request.url_for("google_callback"))
    # Ensure HTTPS in production (behind Cloudflare)
    if "pipka.net" in redirect_uri:
        redirect_uri = redirect_uri.replace("http://", "https://")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback")
async def google_callback(request: Request):
    """Handle Google OAuth callback, create/login user, set session."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        logger.error("OAuth callback failed: %s", e)
        return RedirectResponse(url="/?error=auth_failed")

    userinfo = token.get("userinfo")
    if not userinfo:
        return RedirectResponse(url="/?error=no_userinfo")

    google_sub = userinfo["sub"]
    email = userinfo.get("email", "")
    name = userinfo.get("name", "")
    avatar = userinfo.get("picture", "")

    async with async_session() as session:
        user = await get_or_create_google_user(google_sub, email, name, avatar, session)

        # Session-fixation defense: clear ANY pre-login state before writing
        # the authenticated identity. If an attacker pre-set ``pipka_session``
        # on the victim's browser (subdomain XSS, MITM on plain HTTP, etc.),
        # the cookie they planted now carries no privileges.
        request.session.clear()

        # Store user in session cookie. CSRF token is intentionally rotated
        # by clear() — CSRFMiddleware will mint a fresh one on the next
        # response.
        request.session["user_id"] = user.id
        request.session["user_email"] = user.email
        request.session["user_name"] = user.name or ""
        request.session["user_avatar"] = user.avatar_url or ""
        request.session["user_role"] = user.role

    return RedirectResponse(url="/")


@router.post("/auth/logout")
async def logout(request: Request):
    """Clear session. POST + CSRF-protected — a GET logout was vulnerable
    to forced-logout via ``<img src="/auth/logout">`` embedded in any page
    a logged-in user happened to visit. Returns JSON; the SPA navigates
    on success.
    """
    request.session.clear()
    return {"ok": True}


@router.get("/api/me")
async def get_me(request: Request):
    """Return current user info from session.

    Includes the CSRF token so the SPA can echo it back via the
    ``X-CSRF-Token`` header on unsafe requests. The token is also delivered
    as a JS-readable cookie by ``CSRFMiddleware`` — either source works.
    """
    user_id = request.session.get("user_id")
    csrf_token = request.session.get("csrf_token", "")
    if not user_id:
        return {"authenticated": False, "role": "guest", "csrf_token": csrf_token}

    return {
        "authenticated": True,
        "user_id": user_id,
        "email": request.session.get("user_email", ""),
        "name": request.session.get("user_name", ""),
        "avatar": request.session.get("user_avatar", ""),
        "role": request.session.get("user_role", "user"),
        "csrf_token": csrf_token,
    }
