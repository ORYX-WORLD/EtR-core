from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEFAULT_PRIVATE_KEY = Path("/var/lib/etr-core/bootstrap-private.pem")
DEFAULT_PUBLIC_KEY = Path("/var/lib/etr-core/bootstrap-public.pem")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def canonical_enrollment_request(
    *,
    serial: str,
    hostname: str,
    rotation_token: str = "",
    timestamp: str,
    nonce: str,
) -> bytes:
    body = json.dumps(
        {
            "hostname": str(hostname or ""),
            "rotationToken": str(rotation_token or ""),
            "serial": str(serial or ""),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{timestamp}\n{nonce}\n{body}".encode("utf-8")


def ensure_device_keypair(
    private_path: Path = DEFAULT_PRIVATE_KEY,
    public_path: Path = DEFAULT_PUBLIC_KEY,
) -> tuple[Path, Path]:
    private_path = Path(private_path)
    public_path = Path(public_path)
    private_path.parent.mkdir(parents=True, exist_ok=True)

    if private_path.exists():
        private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise RuntimeError("La clé bootstrap existante n'est pas une clé Ed25519")
    else:
        private_key = Ed25519PrivateKey.generate()
        temporary = private_path.with_suffix(private_path.suffix + ".tmp")
        temporary.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        os.chmod(temporary, 0o600)
        temporary.replace(private_path)

    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    temporary_public = public_path.with_suffix(public_path.suffix + ".tmp")
    temporary_public.write_bytes(public_bytes)
    os.chmod(temporary_public, 0o644)
    temporary_public.replace(public_path)
    os.chmod(private_path, 0o600)
    return private_path, public_path


def load_public_key(public_key_pem: str | bytes) -> Ed25519PublicKey:
    raw = public_key_pem.encode("ascii") if isinstance(public_key_pem, str) else public_key_pem
    key = serialization.load_pem_public_key(raw)
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeError("La clé publique bootstrap n'est pas Ed25519")
    return key


def public_key_fingerprint(public_key_pem: str | bytes) -> str:
    key = load_public_key(public_key_pem)
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def sign_enrollment_request(
    *,
    serial: str,
    hostname: str,
    rotation_token: str = "",
    private_path: Path = DEFAULT_PRIVATE_KEY,
    now: Callable[[], float] = time.time,
    nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> dict[str, str]:
    private_key = serialization.load_pem_private_key(Path(private_path).read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError("La clé bootstrap privée n'est pas Ed25519")
    timestamp = str(int(now()))
    nonce = _base64url(nonce_factory(18))
    payload = canonical_enrollment_request(
        serial=serial,
        hostname=hostname,
        rotation_token=rotation_token,
        timestamp=timestamp,
        nonce=nonce,
    )
    signature = _base64url(private_key.sign(payload))
    return {
        "X-EtR-Timestamp": timestamp,
        "X-EtR-Nonce": nonce,
        "X-EtR-Signature": signature,
    }


def export_public_key(public_path: Path = DEFAULT_PUBLIC_KEY) -> str:
    public_key = load_public_key(Path(public_path).read_bytes())
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
