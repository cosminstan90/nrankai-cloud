import hmac
import os
from fastapi import HTTPException, Security
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
