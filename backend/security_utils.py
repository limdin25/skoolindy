from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

SECRET_PREFIX = "enc:v1:"
DEFAULT_LOCAL_SECRET = "engageflow-local-secret-change-me"


def _secret_material() -> bytes:
    raw = (os.environ.get("ENGAGEFLOW_SECRET_KEY") or "").strip()
    if not raw:
        raw = DEFAULT_LOCAL_SECRET
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _keystream(secret: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(secret + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_encrypted_secret(value: str) -> bool:
    return str(value or "").startswith(SECRET_PREFIX)


def encrypt_secret(value: Optional[str]) -> str:
    text = str(value or "")
    if not text:
        return ""
    if is_encrypted_secret(text):
        return text
    nonce = os.urandom(16)
    secret = _secret_material()
    plain = text.encode("utf-8")
    stream = _keystream(secret, nonce, len(plain))
    cipher = bytes(a ^ b for a, b in zip(plain, stream))
    payload = base64.urlsafe_b64encode(nonce + cipher).decode("ascii")
    return f"{SECRET_PREFIX}{payload}"


def decrypt_secret(value: Optional[str]) -> str:
    text = str(value or "")
    if not text:
        return ""
    if not is_encrypted_secret(text):
        return text
    encoded = text[len(SECRET_PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(blob) < 17:
            return ""
        nonce, cipher = blob[:16], blob[16:]
        secret = _secret_material()
        stream = _keystream(secret, nonce, len(cipher))
        plain = bytes(a ^ b for a, b in zip(cipher, stream))
        return plain.decode("utf-8")
    except Exception:
        return ""


def mask_secret(value: Optional[str]) -> str:
    secret = str(value or "").strip()
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"
