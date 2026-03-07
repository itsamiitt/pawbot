"""Auth middleware for FastAPI (dashboard/gateway) route protection.

Phase 19: Provides:
  - AuthMiddleware      — FastAPI middleware that verifies tokens on requests
  - require_scope()     — Decorator for route-level scope checking
  - get_current_device  — Dependency injection for authenticated routes
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger("pawbot.auth.middleware")

# Try to import FastAPI — may not be available in all contexts
try:
    from fastapi import Depends, HTTPException, Request
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from pawbot.auth.tokens import TokenClaims, TokenService


# ═══════════════════════════════════════════════════════════════════════════════
#  Request Context
# ═══════════════════════════════════════════════════════════════════════════════


class AuthContext:
    """Holds authenticated device info for a request."""

    def __init__(self, claims: TokenClaims) -> None:
        self.claims = claims
        self.device_id = claims.device_id
        self.role = claims.role
        self.scopes = claims.scopes

    def has_scope(self, scope: str) -> bool:
        return self.claims.has_scope(scope)

    def __repr__(self) -> str:
        return f"AuthContext(device={self.device_id[:16]}, role={self.role})"


# ═══════════════════════════════════════════════════════════════════════════════
#  Unprotected Routes
# ═══════════════════════════════════════════════════════════════════════════════

# Routes that don't require authentication
UNPROTECTED_ROUTES = {
    "/health",
    "/api/health",
    "/api/auth/pair",
    "/api/auth/login",
    "/api/auth/token",
    "/docs",
    "/openapi.json",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Token Extraction
# ═══════════════════════════════════════════════════════════════════════════════


def extract_token(request_headers: dict[str, str]) -> str | None:
    """Extract bearer token from request headers.

    Supports:
      - Authorization: Bearer <token>
      - X-Auth-Token: <token>
      - ?token=<token> query parameter
    """
    # Authorization header
    auth = request_headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()

    # Custom header
    custom = request_headers.get("x-auth-token", "")
    if custom:
        return custom.strip()

    return None


def verify_request(
    token_service: TokenService,
    headers: dict[str, str],
    path: str = "",
    required_scopes: list[str] | None = None,
) -> tuple[bool, AuthContext | None, str]:
    """Verify a request's authentication.

    Returns (is_authenticated, auth_context, error_message).
    """
    # Check unprotected routes
    if path in UNPROTECTED_ROUTES:
        return True, None, ""

    # Extract token
    token = extract_token(headers)
    if not token:
        return False, None, "Missing authentication token"

    # Verify token
    claims = token_service.verify(token)
    if not claims:
        return False, None, "Invalid or expired token"

    ctx = AuthContext(claims)

    # Check required scopes
    if required_scopes:
        for scope in required_scopes:
            if not ctx.has_scope(scope):
                return False, ctx, f"Missing required scope: {scope}"

    return True, ctx, ""


# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI Integration
# ═══════════════════════════════════════════════════════════════════════════════


def create_auth_dependency(token_service: TokenService):
    """Create a FastAPI dependency that verifies tokens.

    Usage:
        token_service = TokenService()
        get_auth = create_auth_dependency(token_service)

        @app.get("/api/protected")
        async def protected_route(auth: AuthContext = Depends(get_auth)):
            return {"device": auth.device_id}
    """
    if not HAS_FASTAPI:
        raise ImportError("FastAPI is required for auth dependency")

    security = HTTPBearer(auto_error=False)

    async def _get_auth(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> AuthContext:
        # Check unprotected routes
        if request.url.path in UNPROTECTED_ROUTES:
            return AuthContext(TokenClaims(
                device_id="anonymous",
                role="viewer",
                scopes=["read"],
                issued_at=0,
                expires_at=float("inf"),
                token_id="anonymous",
            ))

        # Try bearer token
        token = None
        if credentials:
            token = credentials.credentials

        # Try custom header
        if not token:
            token = request.headers.get("x-auth-token", "")

        # Try query parameter
        if not token:
            token = request.query_params.get("token", "")

        if not token:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        claims = token_service.verify(token)
        if not claims:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return AuthContext(claims)

    return _get_auth


def require_scope(*scopes: str):
    """Decorator that checks scopes on a FastAPI route handler.

    Usage:
        @app.post("/api/admin/config")
        @require_scope("admin.config")
        async def update_config(auth: AuthContext = Depends(get_auth)):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Find AuthContext in kwargs
            auth: AuthContext | None = None
            for v in kwargs.values():
                if isinstance(v, AuthContext):
                    auth = v
                    break

            if not auth:
                if HAS_FASTAPI:
                    raise HTTPException(
                        status_code=401,
                        detail="Authentication required",
                    )
                raise PermissionError("Authentication required")

            for scope in scopes:
                if not auth.has_scope(scope):
                    if HAS_FASTAPI:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Insufficient permissions. Required scope: {scope}",
                        )
                    raise PermissionError(f"Missing scope: {scope}")

            return await func(*args, **kwargs)
        return wrapper
    return decorator
