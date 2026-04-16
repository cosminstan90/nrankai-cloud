import base64
import hmac
import os
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer(auto_error=False)


def _get_key(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(f"Environment variable {name} is not set")
    return val


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison — prevents timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


def require_n8n_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    expected = _get_key("N8N_API_KEY")
    if credentials is None or not _safe_compare(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def require_worker_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> None:
    expected = _get_key("WORKER_API_KEY")
    if credentials is None or not _safe_compare(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing worker key")


def require_admin(request: Request) -> None:
    """HTTP Basic Auth for /admin/* endpoints.

    Returns 503 if env vars are not configured.
    Returns 401 with WWW-Authenticate header (triggers browser native popup).
    """
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")

    if not username or not password:
        raise HTTPException(
            status_code=503,
            detail="Admin credentials not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD in .env.",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="nrankai admin"'},
        )

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        provided_username, provided_password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": 'Basic realm="nrankai admin"'},
        )

    if not _safe_compare(provided_username, username) or not _safe_compare(provided_password, password):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="nrankai admin"'},
        )
