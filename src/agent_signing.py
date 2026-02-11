"""
Agent identity certificate parsing and action signature verification.

This module is intentionally "offline-first":
- Verify request signatures using the agent-bound X.509 certificate public key
- Optionally validate cert time window (no OCSP / CRL in MVP)
"""

import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_public_key,
)
from cryptography.x509.oid import NameOID


SIGNATURE_VERSION = "MB2"


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fingerprint_sha256(cert: x509.Certificate) -> str:
    raw = cert.fingerprint(hashes.SHA256())
    hex_up = raw.hex().upper()
    return ":".join(hex_up[i:i + 2] for i in range(0, len(hex_up), 2))


def _get_cn(name: x509.Name) -> Optional[str]:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def _get_subject_attr(name: x509.Name, oid: NameOID) -> Optional[str]:
    attrs = name.get_attributes_for_oid(oid)
    for attr in attrs:
        value = getattr(attr, "value", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_first_rdn_attribute_value(name: x509.Name) -> Optional[str]:
    """Get first subject RDN attribute value as-is."""
    try:
        for rdn in name.rdns:
            for attr in rdn:
                value = getattr(attr, "value", None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        return None
    return None


def _get_subject_identity_value(name: x509.Name) -> Optional[str]:
    """
    Find best subject attribute value for identity parsing.
    Prefer values that look like comma-separated identity payloads.
    """
    candidates: list[str] = []
    try:
        for rdn in name.rdns:
            for attr in rdn:
                value = getattr(attr, "value", None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
    except Exception:
        return None

    for value in candidates:
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            return value
    return candidates[0] if candidates else None


def sha256_base64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def build_message(
    *,
    ts: str,
    nonce: str,
    agent_name: str,
    method: str,
    path: str,
    body_sha256_base64: str,
    version: str = SIGNATURE_VERSION,
) -> bytes:
    """
    Build bytes-to-sign.

    Format (v2):
        MB2\\n{ts}\\n{nonce}\\n{agent_name}\\n{method}\\n{path}\\n{body_sha256_b64}\\n
    """
    agent_name = (agent_name or "").strip()
    method = (method or "").upper()
    path = path or ""
    return (
        f"{version}\n{ts}\n{nonce}\n{agent_name}\n{method}\n{path}\n{body_sha256_base64}\n"
    ).encode("utf-8")


def parse_certificate_pem(cert_pem: str) -> Tuple[Optional[x509.Certificate], Optional[str]]:
    if not cert_pem or not cert_pem.strip():
        return None, "empty certificate"
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.strip().encode("utf-8"))
        return cert, None
    except Exception as e:
        return None, f"invalid certificate pem: {e}"


def certificate_meta(cert_pem: str) -> Tuple[Dict[str, Any], Optional[str]]:
    cert, err = parse_certificate_pem(cert_pem)
    if err or not cert:
        return {}, err

    try:
        not_before = cert.not_valid_before
        not_after = cert.not_valid_after
        meta = {
            "fingerprint_sha256": _fingerprint_sha256(cert),
            "serial_number_hex": format(cert.serial_number, "x"),
            "issuer_cn": _get_cn(cert.issuer),
            "subject_cn": _get_cn(cert.subject),
            "subject_serial_number": _get_subject_attr(cert.subject, NameOID.SERIAL_NUMBER),
            "subject_uid": _get_subject_attr(cert.subject, NameOID.USER_ID),
            "subject_ou": _get_subject_attr(cert.subject, NameOID.ORGANIZATIONAL_UNIT_NAME),
            "subject_o": _get_subject_attr(cert.subject, NameOID.ORGANIZATION_NAME),
            "subject_rdn_value": _get_first_rdn_attribute_value(cert.subject),
            "subject_identity_value": _get_subject_identity_value(cert.subject),
            "not_before": _to_utc_iso(not_before),
            "not_after": _to_utc_iso(not_after),
            "public_key_type": cert.public_key().__class__.__name__,
        }
        return meta, None
    except Exception as e:
        return {}, f"failed to parse certificate meta: {e}"


def verify_signature(
    *,
    cert_pem: str,
    signature_b64: str,
    algorithm: str,
    message: bytes,
) -> Tuple[bool, str]:
    cert, err = parse_certificate_pem(cert_pem)
    if err or not cert:
        return False, err or "invalid certificate"

    if not signature_b64:
        return False, "empty signature"

    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False, "signature is not valid base64"

    public_key = cert.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        return False, "certificate public key is not RSA"

    alg = (algorithm or "").lower().strip()
    if alg in ("rsa-sha256", "rsa-v1_5-sha256", "rsassa-pkcs1v15-sha256"):
        pad = padding.PKCS1v15()
        digest = hashes.SHA256()
    elif alg in ("rsa-pss-sha256", "rsassa-pss-sha256"):
        pad = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)
        digest = hashes.SHA256()
    else:
        return False, f"unsupported algorithm: {algorithm}"

    try:
        public_key.verify(sig, message, pad, digest)
        return True, "ok"
    except Exception:
        return False, "signature verification failed"


def check_cert_time_window(cert_pem: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    cert, err = parse_certificate_pem(cert_pem)
    if err or not cert:
        return False, err or "invalid certificate"

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    not_before = cert.not_valid_before
    not_after = cert.not_valid_after
    if not_before.tzinfo is None:
        not_before = not_before.replace(tzinfo=timezone.utc)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)

    if now < not_before:
        return False, "certificate not yet valid"
    if now > not_after:
        return False, "certificate expired"
    return True, "ok"


def public_key_matches_certificate(public_key_pem: str, cert_pem: str) -> Tuple[bool, str]:
    cert, err = parse_certificate_pem(cert_pem)
    if err or not cert:
        return False, err or "invalid certificate"

    if not public_key_pem or not public_key_pem.strip():
        return False, "empty public key"

    try:
        provided = load_pem_public_key(public_key_pem.strip().encode("utf-8"))
    except Exception as e:
        return False, f"invalid public key pem: {e}"

    cert_key = cert.public_key()
    if isinstance(provided, rsa.RSAPublicKey) and isinstance(cert_key, rsa.RSAPublicKey):
        if provided.public_numbers() == cert_key.public_numbers():
            return True, "ok"
        return False, "public key does not match certificate"

    return False, "public key type does not match certificate"


def normalize_public_key_pem(public_key_pem: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate and normalize a PEM public key to SubjectPublicKeyInfo PEM."""
    if not public_key_pem or not public_key_pem.strip():
        return None, "empty public key"
    try:
        key = load_pem_public_key(public_key_pem.strip().encode("utf-8"))
        normalized = key.public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return normalized, None
    except Exception as e:
        return None, f"invalid public key pem: {e}"


def extract_public_key_from_certificate(cert_pem: str) -> Tuple[Optional[str], Optional[str]]:
    cert, err = parse_certificate_pem(cert_pem)
    if err or not cert:
        return None, err or "invalid certificate"
    try:
        key = cert.public_key()
        normalized = key.public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return normalized, None
    except Exception as e:
        return None, f"failed to extract certificate public key: {e}"


def public_key_fingerprint_sha256(public_key_pem: str) -> Tuple[Optional[str], Optional[str]]:
    normalized, err = normalize_public_key_pem(public_key_pem)
    if err or not normalized:
        return None, err or "invalid public key"
    try:
        key = load_pem_public_key(normalized.encode("utf-8"))
        der = key.public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
        digest = hashes.Hash(hashes.SHA256())
        digest.update(der)
        fp = digest.finalize().hex().upper()
        return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)), None
    except Exception as e:
        return None, f"failed to fingerprint public key: {e}"
