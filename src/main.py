#!/usr/bin/env python3
"""
Trustbook API Server

A small Moltbook for agent collaboration on software projects.
"""

import os
import re
import json
import logging
import base64
import binascii
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .models import Agent, Project, ProjectMember, Post, Comment, Webhook, Notification, GitHubWebhook
from .schemas import (
    AgentCreate, AgentIdentityUpdate, AgentResponse, AgentProfileResponse, AgentMembership, RecentPost, RecentComment,
    ProjectCreate, ProjectUpdate, ProjectResponse,
    JoinProject, MemberUpdate, MemberResponse,
    PostCreate, PostUpdate, PostResponse,
    CommentCreate, CommentResponse,
    WebhookCreate, WebhookResponse,
    NotificationResponse,
    GitHubWebhookCreate, GitHubWebhookResponse
)
from .utils import (
    parse_mentions, validate_mentions, trigger_webhooks, create_notifications, 
    create_thread_update_notifications, can_use_all_mention, check_all_mention_rate_limit,
    record_all_mention, create_all_notifications
)
from .ratelimit import rate_limiter, init_rate_limiter
from .github_webhook import verify_signature, process_github_event
from .agent_signing import (
    certificate_meta,
    sha256_base64,
    build_message,
    verify_signature as verify_agent_signature,
    check_cert_time_window,
    normalize_public_key_pem,
    extract_public_key_from_certificate,
    public_key_fingerprint_sha256,
    public_key_matches_certificate,
)


# --- Config ---

ROOT = Path(__file__).parent.parent
config_path = ROOT / "config.yaml"
config = {}
if config_path.exists():
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

DEPLOY_ENV = os.getenv("TRUSTBOOK_ENV", os.getenv("MINIBOOK_ENV", config.get("env", "local")))

def get_env_value(key: str, default: str) -> str:
    values = config.get(f"{key}_by_env")
    if isinstance(values, dict):
        value = values.get(DEPLOY_ENV)
        if value:
            return value
    return config.get(key, default)

HOSTNAME = get_env_value("hostname", "localhost:8080")
DB_PATH = config.get("database", "data/minibook.db")
PUBLIC_URL = get_env_value("public_url", f"http://{HOSTNAME}")
ADMIN_TOKEN = config.get("admin_token", None)
SIGNATURE_VERIFY_LOG_ENABLED = bool(config.get("signature_verify_log_enabled", True))
SIGNATURE_VERIFY_LOG_FILE = str(config.get("signature_verify_log_file", "logs/signature_verify.log"))
SIGNATURE_VERIFY_LOG_MAX_BYTES = int(config.get("signature_verify_log_max_bytes", 5 * 1024 * 1024))
SIGNATURE_VERIFY_LOG_BACKUP_COUNT = int(config.get("signature_verify_log_backup_count", 3))

SIGNATURE_VERIFY_LOGGER = logging.getLogger("trustbook.signature_verify")
SIGNATURE_VERIFY_LOGGER.setLevel(logging.INFO)
SIGNATURE_VERIFY_LOGGER.propagate = False

SessionLocal = None


# --- App ---

def _signature_verify_log_path() -> Path:
    raw_path = (SIGNATURE_VERIFY_LOG_FILE or "").strip() or "logs/signature_verify.log"
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _setup_signature_verify_logger():
    if not SIGNATURE_VERIFY_LOG_ENABLED:
        return

    log_path = _signature_verify_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    target_file = str(log_path.resolve())

    for handler in SIGNATURE_VERIFY_LOGGER.handlers:
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", None) == target_file:
            return

    for handler in list(SIGNATURE_VERIFY_LOGGER.handlers):
        SIGNATURE_VERIFY_LOGGER.removeHandler(handler)
        try:
            handler.close()
        except (OSError, ValueError):
            # Best-effort cleanup to avoid app startup failure.
            pass

    file_handler = RotatingFileHandler(
        target_file,
        maxBytes=SIGNATURE_VERIFY_LOG_MAX_BYTES,
        backupCount=SIGNATURE_VERIFY_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    SIGNATURE_VERIFY_LOGGER.addHandler(file_handler)


def _log_signature_verify(event: str, level: str = "info", **fields):
    if not SIGNATURE_VERIFY_LOG_ENABLED:
        return
    payload = {"event": event, **fields}
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if level == "warning":
        SIGNATURE_VERIFY_LOGGER.warning(message)
    elif level == "error":
        SIGNATURE_VERIFY_LOGGER.error(message)
    else:
        SIGNATURE_VERIFY_LOGGER.info(message)


def _signature_status_cn(status: Optional[str]) -> str:
    mapping = {
        "unsigned": "未签名",
        "verified": "验签通过",
        "invalid": "验签失败",
        "no_cert": "未绑定证书",
        "cert_invalid": "证书无效",
        "cert_expired": "证书已过期",
        "cert_not_yet_valid": "证书尚未生效",
    }
    if not status:
        return "未知状态"
    return mapping.get(status, status)


def _signature_reason_cn(reason: Optional[str]) -> Optional[str]:
    mapping = {
        "ok": "通过",
        "empty signature": "签名为空",
        "signature is not valid base64": "签名不是合法的 base64",
        "certificate public key is not RSA": "证书公钥不是 RSA",
        "signature verification failed": "签名校验失败",
        "certificate not yet valid": "证书尚未生效",
        "certificate expired": "证书已过期",
        "agent has no bound certificate": "Agent 未绑定证书",
    }
    if not reason:
        return None
    if reason.startswith("unsupported algorithm:"):
        return reason.replace("unsupported algorithm:", "不支持的签名算法:")
    if reason.startswith("invalid certificate pem:"):
        return reason.replace("invalid certificate pem:", "证书 PEM 无效:")
    if reason.startswith("failed to parse certificate meta:"):
        return reason.replace("failed to parse certificate meta:", "证书元数据解析失败:")
    return mapping.get(reason, reason)


def _build_mb2_message(
    *,
    ts: str,
    nonce: str,
    agent_name: str,
    method: str,
    path: str,
    body_sha256_base64: str,
    line_ending: str = "\n",
    uppercase_method: bool = True,
) -> bytes:
    normalized_agent_name = (agent_name or "").strip()
    normalized_method = (method or "").upper() if uppercase_method else (method or "")
    normalized_path = path or ""
    return (
        f"MB2{line_ending}{ts}{line_ending}{nonce}{line_ending}"
        f"{normalized_agent_name}{line_ending}{normalized_method}{line_ending}"
        f"{normalized_path}{line_ending}{body_sha256_base64}{line_ending}"
    ).encode("utf-8")


def _truncate_text_for_log(text: Optional[str], max_chars: int = 4000) -> Optional[str]:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    remain = len(text) - max_chars
    return f"{text[:max_chars]}...<已截断 {remain} 字符>"


def _redact_header_value(name: str, value: str) -> str:
    key = (name or "").lower()
    if key in {"authorization", "cookie", "set-cookie", "x-api-key"}:
        if key == "authorization" and value.lower().startswith("bearer "):
            return "Bearer ***"
        return "***"
    return value


def _build_headers_snapshot(request: Request) -> dict:
    headers: dict = {}
    for key, value in request.headers.items():
        headers[key] = _redact_header_value(key, value)
    return headers


def _build_body_debug_payload(body: bytes) -> dict:
    utf8_decode_error = None
    body_utf8 = None
    try:
        body_utf8 = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        utf8_decode_error = str(exc)

    preview_len = min(len(body), 256)
    preview_bytes = body[:preview_len]
    return {
        "body_len": len(body),
        "body_sha256": sha256_base64(body),
        "body_preview_hex": preview_bytes.hex(),
        "body_preview_base64": base64.b64encode(preview_bytes).decode("ascii"),
        "body_preview_truncated": len(body) > preview_len,
        "body_utf8_preview": _truncate_text_for_log(body_utf8, max_chars=2000),
        "body_utf8_decode_error": utf8_decode_error,
        "body_ends_with_lf": body.endswith(b"\n"),
        "body_ends_with_crlf": body.endswith(b"\r\n"),
    }


def _build_body_hash_candidates(body: bytes) -> List[dict]:
    candidates: List[tuple[str, bytes]] = [("raw_body", body)]

    if not body.endswith(b"\n"):
        candidates.append(("raw_body_plus_lf", body + b"\n"))
    if body.endswith(b"\n"):
        candidates.append(("raw_body_strip_last_lf", body[:-1]))
    stripped = body.rstrip(b" \t\r\n")
    if stripped != body:
        candidates.append(("raw_body_rstrip_whitespace", stripped))
    if b"\r\n" in body:
        candidates.append(("raw_body_crlf_to_lf", body.replace(b"\r\n", b"\n")))

    json_error = None
    json_obj = None
    try:
        json_obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        json_error = str(exc)

    if json_obj is not None:
        json_candidates = [
            ("json_default", json.dumps(json_obj).encode("utf-8")),
            ("json_ensure_ascii_false", json.dumps(json_obj, ensure_ascii=False).encode("utf-8")),
            ("json_compact", json.dumps(json_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            ("json_compact_sort_keys", json.dumps(json_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")),
        ]
        candidates.extend(json_candidates)

    dedup: dict = {}
    for name, data in candidates:
        digest = sha256_base64(data)
        if digest in dedup:
            dedup[digest]["source_names"].append(name)
            continue
        dedup[digest] = {
            "name": name,
            "source_names": [name],
            "body_len": len(data),
            "body_sha256": digest,
            "body_preview_utf8": _truncate_text_for_log(
                data.decode("utf-8", errors="replace"),
                max_chars=1000,
            ),
        }

    result = list(dedup.values())
    result.sort(key=lambda x: x["name"])
    return {
        "json_parse_error": json_error,
        "candidates": result,
    }


def _build_signature_compare_payload(
    *,
    algorithm_raw: Optional[str],
    algorithm_normalized: str,
    ts_raw: Optional[str],
    ts_normalized: str,
    nonce_raw: Optional[str],
    nonce_normalized: str,
    signature_base64: str,
    signature_base64_valid: bool,
    signature_decode_error: Optional[str],
    signature_bytes_len: Optional[int],
    method_raw: str,
    path_raw: str,
    query_raw: str,
    agent_name_from_token: str,
    agent_name_from_cert: Optional[str],
    body_len: int,
    body_sha256: str,
) -> dict:
    input_params = {
        "algorithm_header": algorithm_raw,
        "ts_header": ts_raw,
        "nonce_header": nonce_raw,
        "signature_len": len(signature_base64),
        "signature_base64_full": signature_base64,
        "signature_base64_valid": signature_base64_valid,
        "signature_decode_error": signature_decode_error,
        "signature_bytes_len": signature_bytes_len,
        "request_method": method_raw,
        "request_path": path_raw,
        "request_query": query_raw or None,
        "body_len": body_len,
        "body_sha256": body_sha256,
        "agent_name_from_token": agent_name_from_token,
        "agent_name_from_cert": agent_name_from_cert,
    }
    constructed_params = {
        "version": "MB2",
        "algorithm_used": algorithm_normalized,
        "ts_used": ts_normalized,
        "nonce_used": nonce_normalized,
        "agent_name_used": agent_name_from_token,
        "method_used": (method_raw or "").upper(),
        "path_used": path_raw,
        "body_sha256_used": body_sha256,
        "line_ending": "LF(\\n)",
    }

    comparison_hints: List[str] = []
    if (algorithm_raw or "").strip() != algorithm_normalized:
        comparison_hints.append("签名算法头已被去除首尾空白后再参与验签")
    if (ts_raw or "").strip() != ts_normalized:
        comparison_hints.append("X-MB-Signature-Ts 头包含首尾空白，服务端已 trim")
    if (nonce_raw or "").strip() != nonce_normalized:
        comparison_hints.append("X-MB-Signature-Nonce 头包含首尾空白，服务端已 trim")
    if agent_name_from_cert and agent_name_from_cert != agent_name_from_token:
        comparison_hints.append("证书中的 agent 名称与 API Key 对应 agent 名称不一致")
    if query_raw:
        comparison_hints.append("请求 URL 含 query 参数，当前 MB2 规则默认只签 path 不含 query")
    return {
        "input_params": input_params,
        "constructed_params": constructed_params,
        "comparison_hints": comparison_hints,
    }


def _diagnose_signature_mismatch(
    *,
    cert_pem: str,
    signature_b64: str,
    algorithm: str,
    ts: str,
    nonce: str,
    agent_name: str,
    cert_agent_name: Optional[str],
    method_raw: str,
    path_raw: str,
    query_raw: str,
    body_hash_candidates: List[dict],
) -> dict:
    path_with_query = f"{path_raw}?{query_raw}" if query_raw else path_raw
    variants = [
        {
            "name": "服务端当前规则",
            "agent_name": agent_name,
            "method": method_raw,
            "path": path_raw,
            "line_ending": "\\n",
            "uppercase_method": True,
        },
        {
            "name": "路径包含query",
            "agent_name": agent_name,
            "method": method_raw,
            "path": path_with_query,
            "line_ending": "\\n",
            "uppercase_method": True,
        },
        {
            "name": "原始method不转大写",
            "agent_name": agent_name,
            "method": method_raw,
            "path": path_raw,
            "line_ending": "\\n",
            "uppercase_method": False,
        },
        {
            "name": "使用CRLF换行",
            "agent_name": agent_name,
            "method": method_raw,
            "path": path_raw,
            "line_ending": "\\r\\n",
            "uppercase_method": True,
        },
    ]
    if cert_agent_name and cert_agent_name != agent_name:
        variants.append(
            {
                "name": "使用证书中的agent_name",
                "agent_name": cert_agent_name,
                "method": method_raw,
                "path": path_raw,
                "line_ending": "\\n",
                "uppercase_method": True,
            }
        )

    seen = set()
    attempts: List[dict] = []
    for variant in variants:
        key = (
            variant["agent_name"],
            variant["method"],
            variant["path"],
            variant["line_ending"],
            variant["uppercase_method"],
        )
        if key in seen:
            continue
        seen.add(key)
        line_ending = "\r\n" if variant["line_ending"] == "\\r\\n" else "\n"
        for body_candidate in body_hash_candidates:
            candidate_message = _build_mb2_message(
                ts=ts,
                nonce=nonce,
                agent_name=variant["agent_name"],
                method=variant["method"],
                path=variant["path"],
                body_sha256_base64=body_candidate["body_sha256"],
                line_ending=line_ending,
                uppercase_method=bool(variant["uppercase_method"]),
            )
            ok, reason = verify_agent_signature(
                cert_pem=cert_pem,
                signature_b64=signature_b64,
                algorithm=algorithm,
                message=candidate_message,
            )
            attempts.append(
                {
                    "variant": variant["name"],
                    "ok": ok,
                    "reason": reason,
                    "reason_cn": _signature_reason_cn(reason),
                    "params": {
                        "agent_name": variant["agent_name"],
                        "method": variant["method"],
                        "path": variant["path"],
                        "line_ending": variant["line_ending"],
                        "uppercase_method": variant["uppercase_method"],
                        "body_hash_source": body_candidate["name"],
                        "body_hash_source_all_names": body_candidate["source_names"],
                        "body_sha256": body_candidate["body_sha256"],
                    },
                    "message_sha256": sha256_base64(candidate_message),
                }
            )

    matched = [item for item in attempts if item.get("ok")]
    if matched:
        first = matched[0]
        return {
            "matched_variant": first.get("variant"),
            "diagnosis": f"签名在候选规则“{first.get('variant')}”下可通过，可能是客户端构造参数与服务端规则不一致",
            "attempts": attempts,
        }
    return {
        "matched_variant": None,
        "diagnosis": "常见参数/顺序/换行变体均未通过，优先排查请求体字节、body hash、私钥与证书是否匹配",
        "attempts": attempts,
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    global SessionLocal
    SessionLocal = init_db(DB_PATH)
    init_rate_limiter(config)
    _setup_signature_verify_logger()
    yield

app = FastAPI(
    title="Trustbook",
    description="A small Moltbook for agent collaboration",
    version="0.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
static_dir = ROOT / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# --- Dependencies ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_agent(
    authorization: str = Header(None),
    db=Depends(get_db)
) -> Optional[Agent]:
    if not authorization:
        return None
    key = authorization.replace("Bearer ", "").strip()
    return db.query(Agent).filter(Agent.api_key == key).first()


def require_agent(agent: Agent = Depends(get_current_agent)) -> Agent:
    if not agent:
        raise HTTPException(401, "Invalid or missing API key")
    return agent


def require_admin(authorization: str = Header(None)) -> bool:
    """Verify admin token for god mode operations."""
    # TODO: Re-enable for production
    return True
    # if not ADMIN_TOKEN:
    #     raise HTTPException(500, "Admin token not configured")
    # if not authorization:
    #     raise HTTPException(401, "Admin token required")
    # token = authorization.replace("Bearer ", "").strip()
    # if token != ADMIN_TOKEN:
    #     raise HTTPException(403, "Invalid admin token")
    # return True


# --- Agent identity & signature helpers ---

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _get_identity_info(agent: Agent) -> dict:
    """
    Public identity info for UI / API consumers.

    Note: We intentionally do NOT expose full certificate PEM here since it may
    contain sensitive subject fields depending on the issuer policy.
    """
    meta = agent.identity_meta or {}
    has_public_key = bool(getattr(agent, "identity_public_key_pem", None))
    cert_pem = getattr(agent, "identity_cert_pem", None)
    if not cert_pem:
        return {
            "status": "public_key_bound" if has_public_key else "unbound",
            "has_public_key": has_public_key,
            "public_key_fingerprint_sha256": meta.get("public_key_fingerprint_sha256"),
            "public_key_bound_at": meta.get("public_key_bound_at"),
        }

    if not meta.get("fingerprint_sha256"):
        parsed, _ = certificate_meta(cert_pem)
        meta = {**meta, **(parsed or {})}

    status = "verified" if meta.get("verified_at") else "bound"
    return {
        "status": status,
        "has_public_key": has_public_key,
        "fingerprint_sha256": meta.get("fingerprint_sha256"),
        "public_key_fingerprint_sha256": meta.get("public_key_fingerprint_sha256"),
        "public_key_bound_at": meta.get("public_key_bound_at"),
        "issuer_cn": meta.get("issuer_cn"),
        "subject_cn": None,
        "not_before": meta.get("not_before"),
        "not_after": meta.get("not_after"),
        "bound_at": meta.get("bound_at"),
        "verified_at": meta.get("verified_at"),
    }


def _bind_public_key_to_agent(agent: Agent, public_key_pem: str, meta: Optional[dict] = None):
    normalized, err = normalize_public_key_pem(public_key_pem)
    if err or not normalized:
        raise HTTPException(400, f"invalid public_key_pem: {err}")

    fp, fp_err = public_key_fingerprint_sha256(normalized)
    if fp_err:
        raise HTTPException(400, f"invalid public_key_pem: {fp_err}")

    data = dict(meta or {})
    data["public_key_fingerprint_sha256"] = fp
    if not data.get("public_key_bound_at"):
        data["public_key_bound_at"] = _now_iso()

    agent.identity_public_key_pem = normalized
    agent.identity_meta = data


def _normalize_signature_meta(meta: Optional[dict]) -> dict:
    if not meta or not meta.get("status"):
        return {"status": "unsigned"}
    return meta


def _parse_subject_identity_fields(subject_value: Optional[str]) -> dict:
    """
    Parse cert subject value for agent identity.

    Supported formats:
    - "agent_name,owner_id" (legacy)
    - "agent=xxx;owner=yyy" or "agent:xxx|owner:yyy" (key/value)
    - "agent_name" (name-only)
    """
    if not subject_value:
        return {}
    raw = subject_value.strip()
    if not raw:
        return {}
    if "*" in raw and not any(ch.isalpha() for ch in raw):
        return {}
    if raw.isdigit() and len(raw) >= 4:
        return {"cert_owner_id": raw}
    if len(raw) <= 2 and raw.isupper():
        return {}

    # Key/value format: agent=xxx;owner=yyy
    if "=" in raw or ":" in raw:
        fields = {}
        for chunk in re.split(r"[;,|]", raw):
            if not chunk.strip():
                continue
            if "=" in chunk:
                key, value = chunk.split("=", 1)
            elif ":" in chunk:
                key, value = chunk.split(":", 1)
            else:
                continue
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if key in ("agent", "agent_name", "name"):
                fields["cert_agent_name"] = value
            elif key in ("owner", "owner_id", "uid", "user_id", "responsible_id"):
                fields["cert_owner_id"] = value
        if fields:
            return fields

    parts = [p.strip() for p in re.split(r"[,|]", raw) if p.strip()]
    if len(parts) >= 2:
        return {"cert_agent_name": parts[0], "cert_owner_id": parts[1]}
    if len(parts) == 1:
        if parts[0].isdigit() and len(parts[0]) >= 4:
            return {"cert_owner_id": parts[0]}
        if len(parts[0]) <= 2 and parts[0].isupper():
            return {}
        return {"cert_agent_name": parts[0]}
    return {}


def _signature_for_response(meta: Optional[dict], author: Optional[Agent] = None) -> dict:
    """
    Build signature payload for API response.

    Backward compatible: for older rows missing parsed cert fields, enrich from
    author's bound certificate when available.
    """
    data = _normalize_signature_meta(meta)
    if not data or data.get("status") == "unsigned":
        return data

    needs_identity = not data.get("cert_agent_name") or not data.get("cert_owner_id")
    needs_cert_meta = (
        not data.get("cert_serial_number_hex")
        or not data.get("cert_issuer_cn")
        or not data.get("cert_not_before")
        or not data.get("cert_not_after")
    )
    if not needs_identity and not needs_cert_meta:
        return data

    cert_pem = getattr(author, "identity_cert_pem", None) if author else None
    if not cert_pem:
        return data

    cert_meta, cert_err = certificate_meta(cert_pem)
    if cert_err:
        return data

    enriched = dict(data)
    subject_candidates = [
        cert_meta.get("subject_identity_value"),
        cert_meta.get("subject_cn"),
        cert_meta.get("subject_rdn_value"),
    ]
    parsed: dict = {}
    for value in subject_candidates:
        if not value:
            continue
        parsed = _parse_subject_identity_fields(value)
        if parsed:
            break
    if parsed.get("cert_agent_name") and not enriched.get("cert_agent_name"):
        enriched["cert_agent_name"] = parsed["cert_agent_name"]
    if parsed.get("cert_owner_id") and not enriched.get("cert_owner_id"):
        enriched["cert_owner_id"] = parsed["cert_owner_id"]

    if not enriched.get("cert_agent_name") and cert_meta.get("subject_cn"):
        enriched["cert_agent_name"] = cert_meta["subject_cn"]
    if not enriched.get("cert_owner_id"):
        enriched["cert_owner_id"] = (
            cert_meta.get("subject_serial_number")
            or cert_meta.get("subject_uid")
        )

    if cert_meta.get("serial_number_hex") and not enriched.get("cert_serial_number_hex"):
        enriched["cert_serial_number_hex"] = cert_meta["serial_number_hex"]
    if cert_meta.get("issuer_cn") and not enriched.get("cert_issuer_cn"):
        enriched["cert_issuer_cn"] = cert_meta["issuer_cn"]
    if cert_meta.get("not_before") and not enriched.get("cert_not_before"):
        enriched["cert_not_before"] = cert_meta["not_before"]
    if cert_meta.get("not_after") and not enriched.get("cert_not_after"):
        enriched["cert_not_after"] = cert_meta["not_after"]
    return enriched


async def _verify_request_signature(
    *,
    request: Request,
    agent: Agent,
    signature_b64: Optional[str],
    algorithm: Optional[str],
    ts: Optional[str],
    nonce: Optional[str],
) -> dict:
    """
    Verify optional request signature.

    Never blocks the request (MVP: trust-enhancement only). Returns a structured
    dict for persistence and UI display.
    """
    trace_id = uuid4().hex[:12]
    if not signature_b64:
        _log_signature_verify(
            "未签名请求跳过验签",
            trace_id=trace_id,
            agent_id=agent.id,
            agent_name=agent.name,
            method=request.method,
            path=request.url.path,
        )
        return {"status": "unsigned"}

    alg = (algorithm or "rsa-v1_5-sha256").strip()
    ts_s = (ts or "").strip()
    nonce_s = (nonce or "").strip()
    query_s = request.url.query or ""
    signature_base64_valid = False
    signature_decode_error = None
    signature_bytes_len = None
    try:
        decoded_sig = base64.b64decode(signature_b64, validate=True)
        signature_base64_valid = True
        signature_bytes_len = len(decoded_sig)
    except (binascii.Error, ValueError) as exc:
        signature_decode_error = str(exc)
    _log_signature_verify(
        "开始验签",
        trace_id=trace_id,
        agent_id=agent.id,
        agent_name=agent.name,
        method=request.method,
        path=request.url.path,
        algorithm=alg,
        ts=ts_s or None,
        nonce=nonce_s or None,
        signature_len=len(signature_b64),
        signature_preview=(f"{signature_b64[:20]}..." if len(signature_b64) > 20 else signature_b64),
    )
    _log_signature_verify(
        "请求头快照",
        trace_id=trace_id,
        headers=_build_headers_snapshot(request),
    )

    body = await request.body()
    body_sha256 = sha256_base64(body)
    _log_signature_verify(
        "请求体快照",
        trace_id=trace_id,
        **_build_body_debug_payload(body),
    )
    body_hash_debug = _build_body_hash_candidates(body)
    body_hash_candidates = body_hash_debug.get("candidates", [])
    _log_signature_verify(
        "请求体哈希候选",
        trace_id=trace_id,
        json_parse_error=body_hash_debug.get("json_parse_error"),
        candidate_count=len(body_hash_candidates),
        candidates=body_hash_candidates,
    )
    message = build_message(
        ts=ts_s,
        nonce=nonce_s,
        agent_name=agent.name,
        method=request.method,
        path=request.url.path,
        body_sha256_base64=body_sha256,
    )
    _log_signature_verify(
        "验签原文已构造",
        trace_id=trace_id,
        body_len=len(body),
        body_sha256=body_sha256,
        message_sha256=sha256_base64(message),
        canonical_message=message.decode("utf-8", errors="replace"),
    )
    compare_payload = _build_signature_compare_payload(
        algorithm_raw=algorithm,
        algorithm_normalized=alg,
        ts_raw=ts,
        ts_normalized=ts_s,
        nonce_raw=nonce,
        nonce_normalized=nonce_s,
        signature_base64=signature_b64,
        signature_base64_valid=signature_base64_valid,
        signature_decode_error=signature_decode_error,
        signature_bytes_len=signature_bytes_len,
        method_raw=request.method,
        path_raw=request.url.path,
        query_raw=query_s,
        agent_name_from_token=agent.name,
        agent_name_from_cert=None,
        body_len=len(body),
        body_sha256=body_sha256,
    )
    _log_signature_verify(
        "验签参数对比",
        trace_id=trace_id,
        **compare_payload,
    )

    meta: dict = {
        "status": "invalid",
        "algorithm": alg,
        "ts": ts_s or None,
        "nonce": nonce_s or None,
        "method": request.method,
        "path": request.url.path,
        "body_sha256": body_sha256,
        "signature": signature_b64,
        "checked_at": _now_iso(),
    }

    cert_pem = getattr(agent, "identity_cert_pem", None)
    if not cert_pem:
        meta["status"] = "no_cert"
        meta["reason"] = "agent has no bound certificate"
        _log_signature_verify(
            "验签失败_未绑定证书",
            level="warning",
            trace_id=trace_id,
            status=meta["status"],
            status_cn=_signature_status_cn(meta.get("status")),
            reason=meta["reason"],
            reason_cn=_signature_reason_cn(meta.get("reason")),
        )
        return meta

    cert_meta, cert_err = certificate_meta(cert_pem)
    if cert_err:
        meta["status"] = "cert_invalid"
        meta["reason"] = cert_err
        _log_signature_verify(
            "验签失败_证书解析失败",
            level="warning",
            trace_id=trace_id,
            status=meta["status"],
            status_cn=_signature_status_cn(meta.get("status")),
            reason=meta["reason"],
            reason_cn=_signature_reason_cn(meta.get("reason")),
        )
        return meta

    meta["cert_fingerprint_sha256"] = cert_meta.get("fingerprint_sha256")
    meta["cert_serial_number_hex"] = cert_meta.get("serial_number_hex")
    meta["cert_issuer_cn"] = cert_meta.get("issuer_cn")
    meta["cert_not_before"] = cert_meta.get("not_before")
    meta["cert_not_after"] = cert_meta.get("not_after")
    subject_candidates = [
        cert_meta.get("subject_identity_value"),
        cert_meta.get("subject_cn"),
        cert_meta.get("subject_rdn_value"),
    ]
    parsed: dict = {}
    for value in subject_candidates:
        if not value:
            continue
        parsed = _parse_subject_identity_fields(value)
        if parsed:
            break
    if parsed:
        meta.update(parsed)
    if not meta.get("cert_agent_name") and cert_meta.get("subject_cn"):
        meta["cert_agent_name"] = cert_meta.get("subject_cn")
    if not meta.get("cert_owner_id"):
        meta["cert_owner_id"] = (
            cert_meta.get("subject_serial_number")
            or cert_meta.get("subject_uid")
        )
    _log_signature_verify(
        "证书元数据已加载",
        trace_id=trace_id,
        cert_fingerprint_sha256=meta.get("cert_fingerprint_sha256"),
        cert_serial_number_hex=meta.get("cert_serial_number_hex"),
        cert_issuer_cn=meta.get("cert_issuer_cn"),
        cert_not_before=meta.get("cert_not_before"),
        cert_not_after=meta.get("cert_not_after"),
        cert_agent_name=meta.get("cert_agent_name"),
        cert_owner_id=meta.get("cert_owner_id"),
    )
    if meta.get("cert_agent_name") and meta.get("cert_agent_name") != agent.name:
        _log_signature_verify(
            "证书身份与API Key身份不一致",
            level="warning",
            trace_id=trace_id,
            agent_name_from_token=agent.name,
            agent_name_from_cert=meta.get("cert_agent_name"),
        )

    ok_sig, sig_reason = verify_agent_signature(
        cert_pem=cert_pem,
        signature_b64=signature_b64,
        algorithm=alg,
        message=message,
    )
    _log_signature_verify(
        "签名校验完成",
        level="warning" if not ok_sig else "info",
        trace_id=trace_id,
        ok=ok_sig,
        reason=sig_reason,
        reason_cn=_signature_reason_cn(sig_reason),
    )
    if not ok_sig:
        meta["status"] = "invalid"
        meta["reason"] = sig_reason
        mismatch_diagnosis = _diagnose_signature_mismatch(
            cert_pem=cert_pem,
            signature_b64=signature_b64,
            algorithm=alg,
            ts=ts_s,
            nonce=nonce_s,
            agent_name=agent.name,
            cert_agent_name=meta.get("cert_agent_name"),
            method_raw=request.method,
            path_raw=request.url.path,
            query_raw=query_s,
            body_hash_candidates=body_hash_candidates,
        )
        _log_signature_verify(
            "验签失败诊断",
            level="warning",
            trace_id=trace_id,
            diagnosis=mismatch_diagnosis.get("diagnosis"),
            matched_variant=mismatch_diagnosis.get("matched_variant"),
            attempts=mismatch_diagnosis.get("attempts"),
        )
        _log_signature_verify(
            "验签结果",
            level="warning",
            trace_id=trace_id,
            status=meta["status"],
            status_cn=_signature_status_cn(meta.get("status")),
            reason=meta["reason"],
            reason_cn=_signature_reason_cn(meta.get("reason")),
        )
        return meta

    ok_time, time_reason = check_cert_time_window(cert_pem)
    _log_signature_verify(
        "证书有效期校验完成",
        level="warning" if not ok_time else "info",
        trace_id=trace_id,
        ok=ok_time,
        reason=time_reason,
        reason_cn=_signature_reason_cn(time_reason),
    )
    if ok_time:
        meta["status"] = "verified"
        meta["reason"] = None
        _log_signature_verify(
            "验签结果",
            trace_id=trace_id,
            status=meta["status"],
            status_cn=_signature_status_cn(meta.get("status")),
        )
        return meta

    meta["status"] = "cert_expired" if "expired" in time_reason else "cert_not_yet_valid"
    meta["reason"] = time_reason
    _log_signature_verify(
        "验签结果",
        level="warning",
        trace_id=trace_id,
        status=meta["status"],
        status_cn=_signature_status_cn(meta.get("status")),
        reason=meta["reason"],
        reason_cn=_signature_reason_cn(meta.get("reason")),
    )
    return meta


# --- Health & Home ---

@app.get("/health")
async def health():
    return {"status": "ok", "hostname": HOSTNAME}


@app.get("/api/v1/version")
async def version():
    """Get version info including git commit SHA."""
    import subprocess
    git_sha = "unknown"
    git_time = "unknown"
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL
        ).decode().strip()
        git_time = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass
    return {
        "version": "0.1.0",
        "git_sha": git_sha,
        "git_time": git_time,
        "hostname": HOSTNAME
    }


@app.get("/api/v1/site-config")
async def site_config():
    """Public site configuration for frontend."""
    public_url = PUBLIC_URL.rstrip("/")
    skills: Dict[str, str] = {}
    skills_root = ROOT / "skills"
    if skills_root.exists():
        for child in skills_root.iterdir():
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").exists():
                continue
            skill_name = child.name
            skills[skill_name] = f"{public_url}/skill/{skill_name}/SKILL.md"

    return {
        "public_url": public_url,
        # Back-compat: old frontend expects a single skill_url
        "skill_url": skills.get("trustbook", f"{public_url}/skill/trustbook/SKILL.md"),
        # New: expose all available skills for env-specific linking (local/test/proxy)
        "skills": skills,
        "api_docs": f"{public_url}/docs",
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = ROOT / "templates" / "index.html"
    if template_path.exists():
        with open(template_path) as f:
            html = f.read()
        return html.replace("{{hostname}}", HOSTNAME)
    return f"<h1>Trustbook</h1><p>Running at {HOSTNAME}</p>"


@app.get("/skill/trustbook")
async def skill_info():
    return {
        "name": "trustbook",
        "version": "0.1.0",
        "description": "Connect your agent to this Trustbook instance",
        "homepage": PUBLIC_URL.rstrip("/"),
        "files": {"SKILL.md": f"{PUBLIC_URL.rstrip('/')}/skill/trustbook/SKILL.md"},
        "config": {"base_url": PUBLIC_URL.rstrip("/")}
    }


@app.get("/skill/trustbook/SKILL.md", response_class=PlainTextResponse)
async def skill_file():
    skill_path = ROOT / "skills" / "trustbook" / "SKILL.md"
    if skill_path.exists():
        content = skill_path.read_text()
        # Inject public URL
        content = content.replace("{{BASE_URL}}", PUBLIC_URL.rstrip("/"))
        return content
    return "# Trustbook Skill\n\nSkill file not found."


@app.get("/skill/{skill_name}")
async def skill_info_generic(skill_name: str):
    """Generic skill manifest endpoint for any skills/<skill_name>/SKILL.md."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill_name or ""):
        raise HTTPException(400, "Invalid skill name")

    skill_path = ROOT / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise HTTPException(404, "Skill not found")

    public_url = PUBLIC_URL.rstrip("/")
    return {
        "name": skill_name,
        "version": "0.1.0",
        "description": f"Skill {skill_name}",
        "homepage": public_url,
        "files": {"SKILL.md": f"{public_url}/skill/{skill_name}/SKILL.md"},
        "config": {"base_url": public_url}
    }


@app.get("/skill/{skill_name}/SKILL.md", response_class=PlainTextResponse)
async def skill_file_generic(skill_name: str):
    """Generic skill file endpoint for any skills/<skill_name>/SKILL.md."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill_name or ""):
        raise HTTPException(400, "Invalid skill name")

    skill_path = ROOT / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise HTTPException(404, "Skill not found")

    content = skill_path.read_text()
    content = content.replace("{{BASE_URL}}", PUBLIC_URL.rstrip("/"))
    return content


# --- Agents ---

@app.post("/api/v1/agents", response_model=AgentResponse)
async def register_agent(data: AgentCreate, db=Depends(get_db)):
    """Register a new agent. Returns API key (only shown once)."""
    # Rate limit registration by name (to prevent spam)
    rate_limiter.check(f"register:{data.name}", "register")
    
    if db.query(Agent).filter(Agent.name == data.name).first():
        raise HTTPException(400, "Agent name already taken")
    
    agent = Agent(name=data.name)

    if data.certificate_pem:
        if data.public_key_pem:
            ok, reason = public_key_matches_certificate(data.public_key_pem, data.certificate_pem)
            if not ok:
                raise HTTPException(400, f"public_key_pem mismatch: {reason}")

        meta, err = certificate_meta(data.certificate_pem)
        if err:
            raise HTTPException(400, f"invalid certificate_pem: {err}")
        meta["bound_at"] = _now_iso()
        meta.pop("subject_cn", None)  # avoid exposing subject PII by default
        agent.identity_cert_pem = data.certificate_pem.strip()
        cert_public_key_pem, key_err = extract_public_key_from_certificate(data.certificate_pem)
        if key_err or not cert_public_key_pem:
            raise HTTPException(400, f"invalid certificate_pem: {key_err}")
        _bind_public_key_to_agent(agent, data.public_key_pem or cert_public_key_pem, meta)
    elif data.public_key_pem:
        _bind_public_key_to_agent(agent, data.public_key_pem)

    db.add(agent)
    db.commit()
    db.refresh(agent)
    
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        api_key=agent.api_key,
        identity=_get_identity_info(agent),
        created_at=agent.created_at,
    )


@app.get("/api/v1/agents/me", response_model=AgentResponse)
async def get_me(agent: Agent = Depends(require_agent)):
    """Get current agent info."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        identity=_get_identity_info(agent),
        created_at=agent.created_at,
        last_seen=agent.last_seen, online=agent.is_online()
    )


@app.put("/api/v1/agents/me/identity", response_model=AgentResponse)
async def update_my_identity(data: AgentIdentityUpdate, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """
    Bind/replace the agent identity certificate (public).

    This does not change API authentication (still uses API key). It only enables
    optional trust-enhancing signatures for actions like posting/commenting.
    """
    if not data.certificate_pem and not data.public_key_pem:
        raise HTTPException(400, "certificate_pem or public_key_pem is required")

    meta = dict(agent.identity_meta or {})

    if data.certificate_pem:
        if data.public_key_pem:
            ok, reason = public_key_matches_certificate(data.public_key_pem, data.certificate_pem)
            if not ok:
                raise HTTPException(400, f"public_key_pem mismatch: {reason}")

        cert_meta, err = certificate_meta(data.certificate_pem)
        if err:
            raise HTTPException(400, f"invalid certificate_pem: {err}")

        meta.update(cert_meta)
        meta["bound_at"] = _now_iso()
        meta.pop("verified_at", None)
        meta.pop("subject_cn", None)  # avoid exposing subject PII by default
        agent.identity_cert_pem = data.certificate_pem.strip()

        cert_public_key_pem, key_err = extract_public_key_from_certificate(data.certificate_pem)
        if key_err or not cert_public_key_pem:
            raise HTTPException(400, f"invalid certificate_pem: {key_err}")
        _bind_public_key_to_agent(agent, data.public_key_pem or cert_public_key_pem, meta)
    else:
        if agent.identity_cert_pem and data.public_key_pem:
            ok, reason = public_key_matches_certificate(data.public_key_pem, agent.identity_cert_pem)
            if not ok:
                raise HTTPException(400, f"public_key_pem mismatch current certificate: {reason}")
        _bind_public_key_to_agent(agent, data.public_key_pem, meta)

    db.commit()
    db.refresh(agent)

    return AgentResponse(
        id=agent.id,
        name=agent.name,
        identity=_get_identity_info(agent),
        created_at=agent.created_at,
        last_seen=agent.last_seen,
        online=agent.is_online(),
    )


@app.post("/api/v1/agents/heartbeat")
async def heartbeat(agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """
    Send heartbeat to mark agent as online.
    Call this periodically (e.g., every 5 minutes) to maintain online status.
    """
    from datetime import datetime
    agent.last_seen = datetime.utcnow()
    db.commit()
    return {"status": "ok", "last_seen": agent.last_seen.isoformat()}


@app.get("/api/v1/agents/me/ratelimit")
async def get_ratelimit(agent: Agent = Depends(require_agent)):
    """Get rate limit stats for current agent."""
    return rate_limiter.get_stats(agent.id)


@app.get("/api/v1/agents", response_model=List[AgentResponse])
async def list_agents(online_only: bool = False, db=Depends(get_db)):
    """List all agents. Use online_only=true to filter to online agents."""
    agents = db.query(Agent).all()
    if online_only:
        agents = [a for a in agents if a.is_online()]
    return [AgentResponse(
        id=a.id,
        name=a.name,
        identity=_get_identity_info(a),
        created_at=a.created_at,
        last_seen=a.last_seen, online=a.is_online()
    ) for a in agents]


@app.get("/api/v1/agents/by-name/{name}", response_model=AgentProfileResponse)
async def get_agent_by_name(name: str, db=Depends(get_db)):
    """Get agent profile by name. Redirects to /agents/:id/profile."""
    agent = db.query(Agent).filter(Agent.name == name).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return await get_agent_profile(agent.id, db)


@app.get("/api/v1/agents/{agent_id}/profile", response_model=AgentProfileResponse)
async def get_agent_profile(agent_id: str, db=Depends(get_db)):
    """Get full agent profile with memberships and recent activity."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    
    # Get memberships
    memberships = []
    members = db.query(ProjectMember).filter(ProjectMember.agent_id == agent_id).all()
    for m in members:
        project = db.query(Project).filter(Project.id == m.project_id).first()
        if project:
            memberships.append(AgentMembership(
                project_id=project.id,
                project_name=project.name,
                role=m.role,
                is_primary_lead=(project.primary_lead_agent_id == agent_id)
            ))
    
    # Get recent posts (last 5)
    recent_posts = []
    posts = db.query(Post).filter(Post.author_id == agent_id).order_by(Post.created_at.desc()).limit(5).all()
    for p in posts:
        recent_posts.append(RecentPost(
            id=p.id,
            project_id=p.project_id,
            title=p.title,
            type=p.type,
            created_at=p.created_at
        ))
    
    # Get recent comments (last 5)
    recent_comments = []
    comments = db.query(Comment).filter(Comment.author_id == agent_id).order_by(Comment.created_at.desc()).limit(5).all()
    for c in comments:
        post = db.query(Post).filter(Post.id == c.post_id).first()
        recent_comments.append(RecentComment(
            id=c.id,
            post_id=c.post_id,
            post_title=post.title if post else "Unknown",
            content_preview=c.content[:100] + "..." if len(c.content) > 100 else c.content,
            created_at=c.created_at
        ))
    
    return AgentProfileResponse(
        agent=AgentResponse(
            id=agent.id,
            name=agent.name,
            identity=_get_identity_info(agent),
            created_at=agent.created_at,
            last_seen=agent.last_seen,
            online=agent.is_online()
        ),
        memberships=memberships,
        recent_posts=recent_posts,
        recent_comments=recent_comments
    )


# --- Projects ---

@app.post("/api/v1/projects", response_model=ProjectResponse)
async def create_project(data: ProjectCreate, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Create a new project. Creator auto-joins as lead."""
    if db.query(Project).filter(Project.name == data.name).first():
        raise HTTPException(400, "Project name already taken")
    
    project = Project(name=data.name, description=data.description)
    db.add(project)
    db.commit()
    
    member = ProjectMember(agent_id=agent.id, project_id=project.id, role="lead")
    db.add(member)
    db.commit()
    db.refresh(project)
    
    # Set creator as primary lead
    project.primary_lead_agent_id = agent.id
    db.commit()
    
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        primary_lead_agent_id=project.primary_lead_agent_id,
        primary_lead_name=agent.name,
        created_at=project.created_at
    )


@app.get("/api/v1/projects", response_model=List[ProjectResponse])
async def list_projects(db=Depends(get_db)):
    """List all projects."""
    projects = db.query(Project).all()
    return [ProjectResponse(
        id=p.id, name=p.name, description=p.description,
        primary_lead_agent_id=p.primary_lead_agent_id,
        primary_lead_name=p.primary_lead.name if p.primary_lead else None,
        created_at=p.created_at
    ) for p in projects]


@app.get("/api/v1/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db=Depends(get_db)):
    """Get project by ID."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        primary_lead_agent_id=project.primary_lead_agent_id,
        primary_lead_name=project.primary_lead.name if project.primary_lead else None,
        created_at=project.created_at
    )


@app.post("/api/v1/projects/{project_id}/join", response_model=MemberResponse)
async def join_project(project_id: str, data: JoinProject, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Join a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    if db.query(ProjectMember).filter(ProjectMember.agent_id == agent.id, ProjectMember.project_id == project_id).first():
        raise HTTPException(400, "Already a member")
    
    role = (data.role or "member").strip() or "member"
    member = ProjectMember(agent_id=agent.id, project_id=project_id, role=role)
    db.add(member)
    db.commit()
    db.refresh(member)
    
    return MemberResponse(agent_id=agent.id, agent_name=agent.name, role=member.role, joined_at=member.joined_at)


@app.get("/api/v1/projects/{project_id}/members", response_model=List[MemberResponse])
async def list_members(project_id: str, db=Depends(get_db)):
    """List project members with online status."""
    members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
    return [MemberResponse(
        agent_id=m.agent_id, 
        agent_name=m.agent.name, 
        role=m.role, 
        joined_at=m.joined_at,
        last_seen=m.agent.last_seen,
        online=m.agent.is_online()
    ) for m in members]


@app.patch("/api/v1/projects/{project_id}/members/{agent_id}", response_model=MemberResponse)
async def update_member_role(
    project_id: str, 
    agent_id: str, 
    data: MemberUpdate, 
    agent: Agent = Depends(require_agent), 
    db=Depends(get_db)
):
    """Update a member's role. DEPRECATED: Use admin API instead. Returns 403."""
    # Role updates disabled for regular API - use /api/v1/admin/... endpoints
    raise HTTPException(
        403, 
        "Role updates are admin-only. Use /api/v1/admin/projects/{project_id}/members/{agent_id}"
    )
    db.commit()
    db.refresh(target_member)
    
    return MemberResponse(
        agent_id=target_member.agent_id,
        agent_name=target_member.agent.name,
        role=target_member.role,
        joined_at=target_member.joined_at,
        last_seen=target_member.agent.last_seen,
        online=target_member.agent.is_online()
    )


# --- Posts ---

@app.post("/api/v1/projects/{project_id}/posts", response_model=PostResponse)
async def create_post(
    project_id: str,
    data: PostCreate,
    request: Request,
    x_mb_signature: Optional[str] = Header(None, alias="X-MB-Signature"),
    x_mb_signature_alg: Optional[str] = Header(None, alias="X-MB-Signature-Alg"),
    x_mb_signature_ts: Optional[str] = Header(None, alias="X-MB-Signature-Ts"),
    x_mb_signature_nonce: Optional[str] = Header(None, alias="X-MB-Signature-Nonce"),
    agent: Agent = Depends(require_agent),
    db=Depends(get_db),
):
    """Create a new post."""
    # Rate limit posts
    rate_limiter.check(agent.id, "post")
    
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    content = data.get_content()
    raw_mentions, has_all = parse_mentions(content)
    mentions = validate_mentions(db, raw_mentions)
    
    # Handle @all mention
    if has_all:
        allowed, reason = can_use_all_mention(db, agent.id, project_id)
        if not allowed:
            raise HTTPException(403, f"Cannot use @all: {reason}")
        
        rate_ok, wait_seconds = check_all_mention_rate_limit(project_id)
        if not rate_ok:
            raise HTTPException(429, f"@all rate limited. Try again in {wait_seconds // 60} minutes.")
    
    signature_meta = await _verify_request_signature(
        request=request,
        agent=agent,
        signature_b64=x_mb_signature,
        algorithm=x_mb_signature_alg,
        ts=x_mb_signature_ts,
        nonce=x_mb_signature_nonce,
    )

    post = Post(project_id=project_id, author_id=agent.id, title=data.title, content=content, type=data.type)
    post.tags = data.tags
    post.mentions = mentions + (['all'] if has_all else [])
    post.signature_meta = signature_meta
    db.add(post)

    if signature_meta.get("status") == "verified" and agent.identity_cert_pem:
        meta = agent.identity_meta or {}
        if not meta.get("verified_at"):
            meta["verified_at"] = signature_meta.get("checked_at")
            agent.identity_meta = meta

    db.commit()
    db.refresh(post)
    
    # Create individual mention notifications
    if mentions:
        create_notifications(db, mentions, "mention", {"post_id": post.id, "title": post.title, "by": agent.name})
    
    # Create @all notifications
    if has_all:
        record_all_mention(project_id)
        create_all_notifications(db, project_id, agent.id, agent.name, post.id)
    
    await trigger_webhooks(db, project_id, "new_post", {"post_id": post.id, "title": post.title, "author": agent.name})
    
    return PostResponse(
        id=post.id, project_id=post.project_id, author_id=post.author_id, author_name=agent.name,
        title=post.title, content=post.content, type=post.type, status=post.status,
        tags=post.tags, mentions=post.mentions, pinned=(post.pin_order is not None), pin_order=post.pin_order, github_ref=post.github_ref,
        comment_count=0,
        signature=_signature_for_response(post.signature_meta, agent),
        created_at=post.created_at, updated_at=post.updated_at
    )


@app.get("/api/v1/projects/{project_id}/posts", response_model=List[PostResponse])
async def list_posts(project_id: str, status: Optional[str] = None, type: Optional[str] = None, db=Depends(get_db)):
    """List posts (pinned first)."""
    query = db.query(Post).filter(Post.project_id == project_id)
    if status:
        query = query.filter(Post.status == status)
    if type:
        query = query.filter(Post.type == type)
    # Order: pinned posts first (by pin_order asc, nulls last), then by created_at desc
    from sqlalchemy import nullslast
    posts = query.order_by(nullslast(Post.pin_order.asc()), Post.created_at.desc()).all()
    
    # Get comment counts for all posts in one query
    post_ids = [p.id for p in posts]
    comment_counts = {}
    if post_ids:
        from sqlalchemy import func
        counts = db.query(Comment.post_id, func.count(Comment.id)).filter(
            Comment.post_id.in_(post_ids)
        ).group_by(Comment.post_id).all()
        comment_counts = {post_id: count for post_id, count in counts}
    
    return [PostResponse(
        id=p.id, project_id=p.project_id, author_id=p.author_id, author_name=p.author.name,
        title=p.title, content=p.content, type=p.type, status=p.status,
        tags=p.tags, mentions=p.mentions, pinned=(p.pin_order is not None), pin_order=p.pin_order, github_ref=p.github_ref,
        comment_count=comment_counts.get(p.id, 0),
        signature=_signature_for_response(getattr(p, "signature_meta", None), p.author),
        created_at=p.created_at, updated_at=p.updated_at
    ) for p in posts]


@app.get("/api/v1/search", response_model=List[PostResponse])
async def search_posts(
    q: str,
    project_id: Optional[str] = None,
    author: Optional[str] = None,
    tag: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 20,
    db=Depends(get_db)
):
    """
    Search posts by keyword (title + content).
    
    Filters:
    - project_id: limit to specific project
    - author: filter by author name
    - tag: filter by tag
    - type: filter by post type
    """
    query = db.query(Post)
    
    # Keyword search (LIKE on title and content)
    if q:
        search_term = f"%{q}%"
        query = query.filter(
            (Post.title.ilike(search_term)) | (Post.content.ilike(search_term))
        )
    
    # Filters
    if project_id:
        query = query.filter(Post.project_id == project_id)
    if author:
        query = query.join(Agent, Post.author_id == Agent.id).filter(Agent.name.ilike(f"%{author}%"))
    if tag:
        # Search in JSON tags field
        query = query.filter(Post._tags.ilike(f"%{tag}%"))
    if type:
        query = query.filter(Post.type == type)
    
    posts = query.order_by(Post.created_at.desc()).limit(min(limit, 50)).all()
    
    # Get comment counts
    post_ids = [p.id for p in posts]
    comment_counts = {}
    if post_ids:
        from sqlalchemy import func
        counts = db.query(Comment.post_id, func.count(Comment.id)).filter(
            Comment.post_id.in_(post_ids)
        ).group_by(Comment.post_id).all()
        comment_counts = {post_id: count for post_id, count in counts}
    
    return [PostResponse(
        id=p.id, project_id=p.project_id, author_id=p.author_id, author_name=p.author.name,
        title=p.title, content=p.content, type=p.type, status=p.status,
        tags=p.tags, mentions=p.mentions, pinned=(p.pin_order is not None), pin_order=p.pin_order, github_ref=p.github_ref,
        comment_count=comment_counts.get(p.id, 0),
        signature=_signature_for_response(getattr(p, "signature_meta", None), p.author),
        created_at=p.created_at, updated_at=p.updated_at
    ) for p in posts]


@app.get("/api/v1/projects/{project_id}/tags", response_model=List[str])
async def get_project_tags(project_id: str, db=Depends(get_db)):
    """Get all unique tags used in a project's posts."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    posts = db.query(Post).filter(Post.project_id == project_id).all()
    all_tags = set()
    for post in posts:
        if post.tags:
            all_tags.update(post.tags)
    return sorted(list(all_tags))


@app.get("/api/v1/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str, db=Depends(get_db)):
    """Get a post by ID."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    comment_count = db.query(Comment).filter(Comment.post_id == post_id).count()
    return PostResponse(
        id=post.id, project_id=post.project_id, author_id=post.author_id, author_name=post.author.name,
        title=post.title, content=post.content, type=post.type, status=post.status,
        tags=post.tags, mentions=post.mentions, pinned=(post.pin_order is not None), pin_order=post.pin_order, github_ref=post.github_ref,
        comment_count=comment_count,
        signature=_signature_for_response(getattr(post, "signature_meta", None), post.author),
        created_at=post.created_at, updated_at=post.updated_at
    )


@app.patch("/api/v1/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    data: PostUpdate,
    request: Request,
    x_mb_signature: Optional[str] = Header(None, alias="X-MB-Signature"),
    x_mb_signature_alg: Optional[str] = Header(None, alias="X-MB-Signature-Alg"),
    x_mb_signature_ts: Optional[str] = Header(None, alias="X-MB-Signature-Ts"),
    x_mb_signature_nonce: Optional[str] = Header(None, alias="X-MB-Signature-Nonce"),
    agent: Agent = Depends(require_agent),
    db=Depends(get_db),
):
    """Update a post (anyone can update - no permission restrictions)."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    
    old_status = post.status
    affects_signed_content = data.title is not None or data.content is not None or data.tags is not None
    
    if data.title is not None:
        post.title = data.title
    if data.content is not None:
        post.content = data.content
        raw_mentions, has_all = parse_mentions(data.content)
        post.mentions = validate_mentions(db, raw_mentions) + (['all'] if has_all else [])
    if data.status is not None:
        post.status = data.status
    # Handle pin_order (new) and pinned (legacy) 
    if data.pin_order is not None:
        post.pin_order = data.pin_order if data.pin_order >= 0 else None
    elif data.pinned is not None:
        # Legacy: pinned=True → pin_order=0, pinned=False → pin_order=None
        post.pin_order = 0 if data.pinned else None
    if data.tags is not None:
        post.tags = data.tags

    if affects_signed_content:
        signature_meta = await _verify_request_signature(
            request=request,
            agent=agent,
            signature_b64=x_mb_signature,
            algorithm=x_mb_signature_alg,
            ts=x_mb_signature_ts,
            nonce=x_mb_signature_nonce,
        )
        post.signature_meta = signature_meta

        if signature_meta.get("status") == "verified" and agent.identity_cert_pem:
            meta = agent.identity_meta or {}
            if not meta.get("verified_at"):
                meta["verified_at"] = signature_meta.get("checked_at")
                agent.identity_meta = meta
    
    db.commit()
    db.refresh(post)
    
    if data.status and data.status != old_status:
        await trigger_webhooks(db, post.project_id, "status_change", {
            "post_id": post.id, "old_status": old_status, "new_status": data.status, "by": agent.name
        })
    
    comment_count = db.query(Comment).filter(Comment.post_id == post_id).count()
    return PostResponse(
        id=post.id, project_id=post.project_id, author_id=post.author_id, author_name=post.author.name,
        title=post.title, content=post.content, type=post.type, status=post.status,
        tags=post.tags, mentions=post.mentions, pinned=(post.pin_order is not None), pin_order=post.pin_order, github_ref=post.github_ref,
        comment_count=comment_count,
        signature=_signature_for_response(getattr(post, "signature_meta", None), post.author),
        created_at=post.created_at, updated_at=post.updated_at
    )


# --- Comments ---

@app.post("/api/v1/posts/{post_id}/comments", response_model=CommentResponse)
async def create_comment(
    post_id: str,
    data: CommentCreate,
    request: Request,
    x_mb_signature: Optional[str] = Header(None, alias="X-MB-Signature"),
    x_mb_signature_alg: Optional[str] = Header(None, alias="X-MB-Signature-Alg"),
    x_mb_signature_ts: Optional[str] = Header(None, alias="X-MB-Signature-Ts"),
    x_mb_signature_nonce: Optional[str] = Header(None, alias="X-MB-Signature-Nonce"),
    agent: Agent = Depends(require_agent),
    db=Depends(get_db),
):
    """Add a comment (supports nesting via parent_id)."""
    # Rate limit comments
    rate_limiter.check(agent.id, "comment")
    
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "Post not found")
    
    raw_mentions, has_all = parse_mentions(data.content)
    mentions = validate_mentions(db, raw_mentions)
    
    # Handle @all mention
    if has_all:
        allowed, reason = can_use_all_mention(db, agent.id, post.project_id)
        if not allowed:
            raise HTTPException(403, f"Cannot use @all: {reason}")
        
        rate_ok, wait_seconds = check_all_mention_rate_limit(post.project_id)
        if not rate_ok:
            raise HTTPException(429, f"@all rate limited. Try again in {wait_seconds // 60} minutes.")
    
    signature_meta = await _verify_request_signature(
        request=request,
        agent=agent,
        signature_b64=x_mb_signature,
        algorithm=x_mb_signature_alg,
        ts=x_mb_signature_ts,
        nonce=x_mb_signature_nonce,
    )

    comment = Comment(post_id=post_id, author_id=agent.id, parent_id=data.parent_id, content=data.content)
    comment.mentions = mentions + (['all'] if has_all else [])
    comment.signature_meta = signature_meta
    db.add(comment)
    
    # Update post's updated_at to reflect new activity
    from datetime import datetime
    post.updated_at = datetime.utcnow()

    if signature_meta.get("status") == "verified" and agent.identity_cert_pem:
        meta = agent.identity_meta or {}
        if not meta.get("verified_at"):
            meta["verified_at"] = signature_meta.get("checked_at")
            agent.identity_meta = meta
    
    db.commit()
    db.refresh(comment)
    
    # Create individual mention notifications
    if mentions:
        create_notifications(db, mentions, "mention", {"post_id": post_id, "comment_id": comment.id, "by": agent.name})
    
    # Create @all notifications
    if has_all:
        record_all_mention(post.project_id)
        create_all_notifications(db, post.project_id, agent.id, agent.name, post_id, comment.id)
    
    # Notify post author
    if post.author_id != agent.id:
        notif = Notification(agent_id=post.author_id, type="reply")
        notif.payload = {"post_id": post_id, "comment_id": comment.id, "by": agent.name}
        db.add(notif)
        db.commit()
    
    # Notify thread participants (excluding commenter, post author, and @mentioned)
    create_thread_update_notifications(db, post, comment.id, agent.id, agent.name, mentions)
    
    await trigger_webhooks(db, post.project_id, "new_comment", {"post_id": post_id, "comment_id": comment.id, "author": agent.name})
    
    return CommentResponse(
        id=comment.id, post_id=comment.post_id, author_id=comment.author_id, author_name=agent.name,
        parent_id=comment.parent_id,
        content=comment.content,
        mentions=comment.mentions,
        signature=_signature_for_response(comment.signature_meta, agent),
        created_at=comment.created_at,
    )


@app.get("/api/v1/posts/{post_id}/comments", response_model=List[CommentResponse])
async def list_comments(post_id: str, db=Depends(get_db)):
    """List comments on a post."""
    comments = db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.created_at).all()
    return [CommentResponse(
        id=c.id, post_id=c.post_id, author_id=c.author_id, author_name=c.author.name,
        parent_id=c.parent_id,
        content=c.content,
        mentions=c.mentions,
        signature=_signature_for_response(getattr(c, "signature_meta", None), c.author),
        created_at=c.created_at,
    ) for c in comments]


# --- Webhooks ---

@app.post("/api/v1/projects/{project_id}/webhooks", response_model=WebhookResponse)
async def create_webhook(project_id: str, data: WebhookCreate, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Create a webhook for project events."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    webhook = Webhook(project_id=project_id, url=data.url)
    webhook.events = data.events
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    
    return WebhookResponse(id=webhook.id, project_id=webhook.project_id, url=webhook.url, events=webhook.events, active=webhook.active)


@app.get("/api/v1/projects/{project_id}/webhooks", response_model=List[WebhookResponse])
async def list_webhooks(project_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """List webhooks for a project."""
    webhooks = db.query(Webhook).filter(Webhook.project_id == project_id).all()
    return [WebhookResponse(id=w.id, project_id=w.project_id, url=w.url, events=w.events, active=w.active) for w in webhooks]


@app.delete("/api/v1/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Delete a webhook."""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    db.delete(webhook)
    db.commit()
    return {"status": "deleted"}


# --- Notifications ---

@app.get("/api/v1/notifications", response_model=List[NotificationResponse])
async def list_notifications(unread_only: bool = False, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """List notifications for current agent."""
    query = db.query(Notification).filter(Notification.agent_id == agent.id)
    if unread_only:
        query = query.filter(Notification.read == False)
    notifications = query.order_by(Notification.created_at.desc()).limit(50).all()
    return [NotificationResponse(id=n.id, type=n.type, payload=n.payload, read=n.read, created_at=n.created_at) for n in notifications]


@app.post("/api/v1/notifications/{notification_id}/read")
async def mark_read(notification_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Mark notification as read."""
    notif = db.query(Notification).filter(Notification.id == notification_id, Notification.agent_id == agent.id).first()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.read = True
    db.commit()
    return {"status": "read"}


@app.post("/api/v1/notifications/read-all")
async def mark_all_read(agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Mark all notifications as read."""
    db.query(Notification).filter(Notification.agent_id == agent.id, Notification.read == False).update({Notification.read: True})
    db.commit()
    return {"status": "all read"}


# --- GitHub Webhooks ---

SYSTEM_AGENT_NAME = "GitHubBot"  # System agent for GitHub-created posts

def get_or_create_system_agent(db) -> Agent:
    """Get or create the system agent for GitHub posts."""
    agent = db.query(Agent).filter(Agent.name == SYSTEM_AGENT_NAME).first()
    if not agent:
        agent = Agent(name=SYSTEM_AGENT_NAME)
        db.add(agent)
        db.commit()
        db.refresh(agent)
    return agent


@app.post("/api/v1/projects/{project_id}/github-webhook", response_model=GitHubWebhookResponse)
async def create_github_webhook(
    project_id: str,
    data: GitHubWebhookCreate,
    agent: Agent = Depends(require_agent),
    db=Depends(get_db)
):
    """Configure GitHub webhook for a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    # Check if config already exists
    existing = db.query(GitHubWebhook).filter(GitHubWebhook.project_id == project_id).first()
    if existing:
        raise HTTPException(400, "GitHub webhook already configured. Use PATCH to update.")
    
    config = GitHubWebhook(
        project_id=project_id,
        secret=data.secret
    )
    config.events = data.events
    config.labels = data.labels
    db.add(config)
    db.commit()
    db.refresh(config)
    
    return GitHubWebhookResponse(
        id=config.id,
        project_id=config.project_id,
        events=config.events,
        labels=config.labels,
        active=config.active
    )


@app.get("/api/v1/projects/{project_id}/github-webhook", response_model=GitHubWebhookResponse)
async def get_github_webhook(project_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Get GitHub webhook config for a project."""
    config = db.query(GitHubWebhook).filter(GitHubWebhook.project_id == project_id).first()
    if not config:
        raise HTTPException(404, "GitHub webhook not configured")
    
    return GitHubWebhookResponse(
        id=config.id,
        project_id=config.project_id,
        events=config.events,
        labels=config.labels,
        active=config.active
    )


@app.delete("/api/v1/projects/{project_id}/github-webhook")
async def delete_github_webhook(project_id: str, agent: Agent = Depends(require_agent), db=Depends(get_db)):
    """Delete GitHub webhook config."""
    config = db.query(GitHubWebhook).filter(GitHubWebhook.project_id == project_id).first()
    if not config:
        raise HTTPException(404, "GitHub webhook not configured")
    db.delete(config)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/v1/github-webhook/{project_id}")
async def receive_github_webhook(project_id: str, request: Request, db=Depends(get_db)):
    """
    Receive GitHub webhook events.
    
    Configure this URL in your GitHub repo webhook settings:
    POST https://your-trustbook-host/api/v1/github-webhook/{project_id}
    
    Set content type to application/json and provide your secret.
    """
    # Get config
    config = db.query(GitHubWebhook).filter(
        GitHubWebhook.project_id == project_id,
        GitHubWebhook.active == True
    ).first()
    if not config:
        raise HTTPException(404, "GitHub webhook not configured for this project")
    
    # Verify signature
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    
    if not verify_signature(body, signature, config.secret):
        raise HTTPException(401, "Invalid signature")
    
    # Get event type
    event_type = request.headers.get("X-GitHub-Event", "")
    if not event_type:
        raise HTTPException(400, "Missing X-GitHub-Event header")
    
    # Parse payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
    
    # Get or create system agent
    system_agent = get_or_create_system_agent(db)
    
    # Process the event
    result = process_github_event(db, config, event_type, payload, system_agent)
    
    if result:
        return {"status": "processed", **result}
    else:
        return {"status": "skipped", "reason": "Event filtered or not applicable"}


# --- Role Descriptions ---

@app.get("/api/v1/projects/{project_id}/roles")
async def get_role_descriptions(project_id: str, db=Depends(get_db)):
    """Get role descriptions for a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return {"roles": project.role_descriptions}


@app.put("/api/v1/projects/{project_id}/roles")
async def set_role_descriptions(
    project_id: str,
    roles: dict,
    db=Depends(get_db)
):
    """Set role descriptions for a project. Body: {"Lead": "desc", "Developer": "desc", ...}"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    project.role_descriptions = roles
    db.commit()
    
    return {"roles": project.role_descriptions}


# --- Grand Plan ---

@app.get("/api/v1/projects/{project_id}/plan", response_model=PostResponse)
async def get_plan(project_id: str, db=Depends(get_db)):
    """Get the project's Grand Plan (unique roadmap post)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    plan = db.query(Post).filter(
        Post.project_id == project_id,
        Post.type == "plan"
    ).first()
    
    if not plan:
        raise HTTPException(404, "No Grand Plan set for this project")
    
    comment_count = db.query(Comment).filter(Comment.post_id == plan.id).count()
    
    return PostResponse(
        id=plan.id, project_id=plan.project_id, author_id=plan.author_id,
        author_name=plan.author.name, title=plan.title, content=plan.content,
        type=plan.type, status=plan.status, tags=plan.tags, mentions=plan.mentions,
        pinned=(plan.pin_order is not None), pin_order=plan.pin_order, github_ref=plan.github_ref, comment_count=comment_count,
        signature=_signature_for_response(getattr(plan, "signature_meta", None), plan.author),
        created_at=plan.created_at, updated_at=plan.updated_at
    )


@app.put("/api/v1/projects/{project_id}/plan", response_model=PostResponse)
async def set_plan(
    project_id: str, 
    title: str = "Grand Plan",
    content: str = "",
    _: bool = Depends(require_admin),
    db=Depends(get_db)
):
    """Create or update the project's Grand Plan (admin only via ADMIN_TOKEN)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    # Admin-only endpoint - require_admin dependency handles auth
    # Use system agent as author for admin-created plans
    author = get_or_create_system_agent(db)
    
    # Find existing plan
    plan = db.query(Post).filter(
        Post.project_id == project_id,
        Post.type == "plan"
    ).first()
    
    if plan:
        # Update existing
        plan.title = title
        plan.content = content
        plan.pin_order = 0  # Plans are always pinned at top
        plan.author_id = author.id  # Update author to whoever edited it
    else:
        # Create new
        plan = Post(
            project_id=project_id,
            author_id=author.id,
            title=title,
            content=content,
            type="plan",
            pin_order=0  # Plans are always pinned at top
        )
        db.add(plan)
    
    db.commit()
    db.refresh(plan)
    
    return PostResponse(
        id=plan.id, project_id=plan.project_id, author_id=plan.author_id,
        author_name=plan.author.name, title=plan.title, content=plan.content,
        type=plan.type, status=plan.status, tags=plan.tags, mentions=plan.mentions,
        pinned=(plan.pin_order is not None), pin_order=plan.pin_order, github_ref=plan.github_ref, comment_count=0,
        signature=_signature_for_response(getattr(plan, "signature_meta", None), plan.author),
        created_at=plan.created_at, updated_at=plan.updated_at
    )


# --- Admin API (God Mode) ---

@app.get("/api/v1/admin/projects", response_model=List[ProjectResponse])
async def admin_list_projects(_: bool = Depends(require_admin), db=Depends(get_db)):
    """List all projects (admin only)."""
    projects = db.query(Project).all()
    return [ProjectResponse(
        id=p.id, name=p.name, description=p.description,
        primary_lead_agent_id=p.primary_lead_agent_id,
        primary_lead_name=p.primary_lead.name if p.primary_lead else None,
        created_at=p.created_at
    ) for p in projects]


@app.get("/api/v1/admin/projects/{project_id}", response_model=ProjectResponse)
async def admin_get_project(project_id: str, _: bool = Depends(require_admin), db=Depends(get_db)):
    """Get project details (admin only)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        primary_lead_agent_id=project.primary_lead_agent_id,
        primary_lead_name=project.primary_lead.name if project.primary_lead else None,
        created_at=project.created_at
    )


@app.patch("/api/v1/admin/projects/{project_id}", response_model=ProjectResponse)
async def admin_update_project(
    project_id: str, 
    data: ProjectUpdate, 
    _: bool = Depends(require_admin), 
    db=Depends(get_db)
):
    """Update project settings like primary lead (admin only)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    if data.primary_lead_agent_id is not None:
        # Verify the agent is a project member
        if data.primary_lead_agent_id != "":
            member = db.query(ProjectMember).filter(
                ProjectMember.project_id == project_id,
                ProjectMember.agent_id == data.primary_lead_agent_id
            ).first()
            if not member:
                raise HTTPException(400, "Agent must be a project member to be primary lead")
            project.primary_lead_agent_id = data.primary_lead_agent_id
        else:
            project.primary_lead_agent_id = None
    
    db.commit()
    db.refresh(project)
    
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        primary_lead_agent_id=project.primary_lead_agent_id,
        primary_lead_name=project.primary_lead.name if project.primary_lead else None,
        created_at=project.created_at
    )


@app.get("/api/v1/admin/projects/{project_id}/members", response_model=List[MemberResponse])
async def admin_list_members(project_id: str, _: bool = Depends(require_admin), db=Depends(get_db)):
    """List project members (admin only)."""
    members = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
    return [MemberResponse(
        agent_id=m.agent_id, 
        agent_name=m.agent.name, 
        role=m.role, 
        joined_at=m.joined_at,
        last_seen=m.agent.last_seen,
        online=m.agent.is_online()
    ) for m in members]


@app.patch("/api/v1/admin/projects/{project_id}/members/{agent_id}", response_model=MemberResponse)
async def admin_update_member_role(
    project_id: str, 
    agent_id: str, 
    data: MemberUpdate, 
    _: bool = Depends(require_admin), 
    db=Depends(get_db)
):
    """Update a member's role (admin only)."""
    member = db.query(ProjectMember).filter(
        ProjectMember.agent_id == agent_id,
        ProjectMember.project_id == project_id
    ).first()
    if not member:
        raise HTTPException(404, "Member not found in this project")
    
    member.role = data.role
    db.commit()
    db.refresh(member)
    
    return MemberResponse(
        agent_id=member.agent_id,
        agent_name=member.agent.name,
        role=member.role,
        joined_at=member.joined_at,
        last_seen=member.agent.last_seen,
        online=member.agent.is_online()
    )


@app.delete("/api/v1/admin/projects/{project_id}/members/{agent_id}")
async def admin_remove_member(
    project_id: str, 
    agent_id: str, 
    _: bool = Depends(require_admin), 
    db=Depends(get_db)
):
    """Remove a member from project (admin only)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")
    
    member = db.query(ProjectMember).filter(
        ProjectMember.agent_id == agent_id,
        ProjectMember.project_id == project_id
    ).first()
    if not member:
        raise HTTPException(404, "Member not found in this project")
    
    # Check if removing primary lead
    if project.primary_lead_agent_id == agent_id:
        raise HTTPException(
            409, 
            "Cannot remove primary lead. Set a new primary lead first."
        )
    
    db.delete(member)
    db.commit()
    
    return {"status": "removed", "agent_id": agent_id, "project_id": project_id}


@app.get("/api/v1/admin/agents", response_model=List[AgentResponse])
async def admin_list_agents(_: bool = Depends(require_admin), db=Depends(get_db)):
    """List all agents (admin only)."""
    agents = db.query(Agent).all()
    return [AgentResponse(
        id=a.id, name=a.name, created_at=a.created_at,
        last_seen=a.last_seen, online=a.is_online()
    ) for a in agents]


# --- Run ---

def run():
    import uvicorn
    port = config.get("port", 8080)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
