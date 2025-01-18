import logging
from typing import Optional

from fastapi import APIRouter, Form, HTTPException
from pydantic import BaseModel
from starlette.authentication import requires
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.status import HTTP_302_FOUND

from khoj.database.adapters import (
    acreate_khoj_token,
    delete_khoj_token,
    get_khoj_tokens,
)
from khoj.database.models import KhojUser
from khoj.routers.helpers import get_next_url

logger = logging.getLogger(__name__)

basic_auth_router = APIRouter()


class TokenRequest(BaseModel):
    name: Optional[str] = None


@basic_auth_router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Handle login with username/password"""
    # Get user from database
    user = await KhojUser.objects.filter(username=username).afirst()

    # Verify password
    if user and user.check_password(password):
        # Set session
        request.session["user"] = {"username": username}
        # Get next URL or default to home
        next_url = get_next_url(request) or "/"
        return RedirectResponse(url=next_url, status_code=HTTP_302_FOUND)

    # Invalid credentials
    raise HTTPException(status_code=401, detail="Invalid username or password")


@basic_auth_router.get("/oauth/metadata")
async def oauth_metadata(request: Request):
    """Return empty metadata to signal basic auth mode"""
    return JSONResponse({"google": None})


@basic_auth_router.post("/token")
@requires(["authenticated"], redirect="login")
async def generate_token(request: Request, token_request: TokenRequest):
    """Generate API token for user"""
    token = await acreate_khoj_token(user=request.user.object, name=token_request.name)
    return {
        "token": token.token,
        "name": token.name,
    }


@basic_auth_router.get("/token")
@requires(["authenticated"], redirect="login")
def get_tokens(request: Request):
    """Get all API tokens for user"""
    tokens = get_khoj_tokens(user=request.user.object)
    return tokens


@basic_auth_router.delete("/token")
@requires(["authenticated"], redirect="login")
async def delete_token(request: Request, token: str):
    """Delete API token"""
    return await delete_khoj_token(user=request.user.object, token=token)


@basic_auth_router.get("/logout")
async def logout(request: Request):
    """Handle logout"""
    request.session.pop("user", None)
    return RedirectResponse(url="/")
