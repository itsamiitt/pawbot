# Phase 6 — Security Hardening

> **Goal:** Close real security gaps with rate limiting, configurable risk, auth, and secret scanning.  
> **Duration:** 7-10 days | **Risk:** Medium | **Depends On:** Phase 0, Phase 1

## Prerequisites

```bash
pip install "slowapi>=0.1.9" "python-jose[cryptography]>=3.3.0"
```

---

## 6.1 — API Rate Limiting

Add `slowapi` to gateway and dashboard servers.

**File:** `pawbot/gateway/server.py`

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    pass  # existing

@app.post("/api/chat")
@limiter.limit("10/minute")
def chat(body: dict, request: Request):
    pass  # existing
```

---

## 6.2 — Dashboard JWT Authentication

**Create:** `pawbot/dashboard/auth.py`

```python
"""Dashboard JWT authentication."""
import hashlib, os, secrets, time, json
from pathlib import Path
from jose import jwt, JWTError

JWT_SECRET_FILE = Path.home() / ".pawbot" / "dashboard_secret"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
AUTH_FILE = Path.home() / ".pawbot" / "dashboard_auth.json"

def _get_or_create_secret() -> str:
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text().strip()
    secret = secrets.token_urlsafe(32)
    JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    JWT_SECRET_FILE.write_text(secret)
    return secret

def verify_password(password: str) -> bool:
    if not AUTH_FILE.exists():
        return False
    data = json.loads(AUTH_FILE.read_text())
    return secrets.compare_digest(
        data.get("password_hash", ""),
        hashlib.sha256(password.encode()).hexdigest()
    )

def create_token(username: str = "admin") -> str:
    payload = {"sub": username, "iat": int(time.time()),
               "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600}
    return jwt.encode(payload, _get_or_create_secret(), algorithm=JWT_ALGORITHM)

def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _get_or_create_secret(), algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
```

**Add auth middleware to dashboard:**

```python
# pawbot/dashboard/server.py
from starlette.middleware.base import BaseHTTPMiddleware

class AuthMiddleware(BaseHTTPMiddleware):
    PUBLIC_PATHS = {"/", "/api/auth/login", "/favicon.ico"}
    async def dispatch(self, request, call_next):
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)
        token = request.cookies.get("pawbot_session")
        if not token or not verify_token(token):
            return JSONResponse(status_code=401, content={"error": "Auth required"})
        return await call_next(request)

@app.post("/api/auth/login")
def login(body: dict):
    if not verify_password(body.get("password", "")):
        return JSONResponse(status_code=401, content={"error": "Invalid password"})
    response = JSONResponse(content={"success": True})
    response.set_cookie("pawbot_session", create_token(), httponly=True, samesite="strict")
    return response
```

---

## 6.3 — Configurable Risk Levels

**File:** `pawbot/config/schema.py` — add:
```python
class SecurityConfig(BaseModel):
    enabled: bool = True
    risk_overrides: dict[str, str] = Field(default_factory=dict)
```

**File:** `pawbot/agent/security.py` — update `ActionGate`:
```python
def _get_risk_level(self, tool_name: str) -> str:
    if tool_name in self._risk_overrides:
        return self._risk_overrides[tool_name]
    return self.RISK_MAP.get(tool_name, ActionRisk.CAUTION)
```

---

## 6.4 — Audit Log Rotation

```python
# In SecurityAuditLog, add rotation before each write:
import gzip, shutil

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

def _rotate_if_needed(self) -> None:
    if not os.path.exists(self.log_path):
        return
    if os.path.getsize(self.log_path) < self.MAX_SIZE_BYTES:
        return
    with open(self.log_path, 'rb') as f_in:
        with gzip.open(f"{self.log_path}.1.gz", 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    open(self.log_path, 'w').close()
```

---

## 6.5 — Output Secret Scanning

**Create:** `pawbot/agent/output_sanitizer.py`

```python
"""Scan agent outputs for leaked secrets before sending."""
import re

SECRET_PATTERNS = [
    ("AWS Key", re.compile(r'AKIA[0-9A-Z]{16}')),
    ("GitHub Token", re.compile(r'ghp_[A-Za-z0-9]{36}')),
    ("OpenAI Key", re.compile(r'sk-[A-Za-z0-9]{48}')),
    ("Anthropic Key", re.compile(r'sk-ant-[A-Za-z0-9-]{90,}')),
    ("Slack Token", re.compile(r'xox[boaprs]-[A-Za-z0-9-]{10,}')),
    ("Private Key", re.compile(r'-----BEGIN (?:RSA )?PRIVATE KEY-----')),
]

def scan_output(text: str) -> list[tuple[str, int]]:
    found = []
    for name, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            found.append((name, m.start()))
    return found

def redact_secrets(text: str) -> str:
    for name, pattern in SECRET_PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text
```

**Integration in `agent/loop.py`** — before returning `final_content`:
```python
from pawbot.agent.output_sanitizer import scan_output, redact_secrets
leaks = scan_output(final_content or "")
if leaks:
    logger.warning("Detected {} secret(s) in output, redacting", len(leaks))
    final_content = redact_secrets(final_content or "")
```

---

## Verification Checklist

- [ ] Gateway endpoints have rate limiting
- [ ] Dashboard login works with JWT httpOnly cookies
- [ ] Risk levels configurable via `config.json`
- [ ] Audit log rotates at 50MB
- [ ] Output secret scanner detects and redacts common key formats
- [ ] All tests pass: `pytest tests/ -v --tb=short`
