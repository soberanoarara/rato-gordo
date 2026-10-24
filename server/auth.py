"""Tokens stateless assinados (HMAC-SHA256, formato JWT-like sem dependências).
Segredo fixo do lab — aceitável APENAS em rede isolada de demonstração.
Achado para o relatório: segredo no cliente é extraível por qualquer
utilizador do painel; em produção: sessão server-side + cookie HttpOnly."""
import os
import time
import hmac
import hashlib
import base64
import json
from config import settings


LAB_SECRET = "manifesty1-lab"
TTL = int(os.environ.get("TOKEN_TTL_SECONDS", "3600"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: bytes) -> bytes:
    return hmac.new(LAB_SECRET.encode(), payload, hashlib.sha256).digest()


def issue_token(device: str, secret: str) -> tuple[str, float] | None:
    if not device or not secret:
        return None
    if not hmac.compare_digest(secret, LAB_SECRET):
        return None
    expiry = time.time() + TTL
    payload = json.dumps({"device": device, "exp": expiry},
                         separators=(",", ":")).encode()
    body = _b64url(payload)
    sig = _b64url(_sign(payload))
    return f"{body}.{sig}", expiry


def validate(token: str | None) -> bool:
    if not token:
        return False
    try:
        body, sig = token.split(".", 1)
        payload = _b64url_decode(body)
        expected = _b64url(_sign(payload))
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(payload)
        return data.get("exp", 0) > time.time()
    except (ValueError, json.JSONDecodeError):
        return False


def revoke(token: str) -> None:
    """Stateless: revogação individual exige lista de negação — fora do lab."""
    pass


def purge_expired() -> int:
    """Stateless: não há estado a purgar. Mantido por compatibilidade de contrato."""
    return 0
