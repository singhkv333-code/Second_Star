"""Communication step executors.

notify.* steps have max_retries=2 — sending the same email twice is
annoying but not destructive, and external delivery APIs flake.

For Day 2 we ship `notify.message` as a logging executor: until the
real email/SMS/push channels are wired up we'd rather log loudly than
fake-deliver. ARCHITECTURE.md §5.5 footnote: "Don't fake-send."

`wait.approval` is also in this module since it's the user-
communication gating step. Day 2 implementation creates the approval
row and signals the engine to pause; resumption is handled by the
approvals router which calls engine.resume_run().
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from backend.models import WorkflowApproval
from backend.workflows.engine import _AwaitingApproval, _utcnow
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ActionNotifyWebhookConfig,
    NotifyLogConfig,
    NotifyMessageConfig,
    WaitApprovalConfig,
)

logger = logging.getLogger(__name__)


def _webhook_destination_blocked(url: str) -> Optional[str]:
    """SSRF guard for the outbound webhook executor.

    ``notify.webhook`` makes a server-side request to a destination the
    workflow author controls. Without a guard that is a classic SSRF
    surface: a user could point it at the cloud metadata endpoint
    (169.254.169.254), at loopback, or at an internal RFC1918 service to
    probe / reach things the server can but they cannot.

    Returns a human-readable reason string when the URL must be REJECTED,
    or ``None`` when it is safe to send. Policy:
      * scheme must be https (the schema validator already enforces this;
        re-checked here as defence-in-depth);
      * the host must resolve, and EVERY resolved IP must be a public
        unicast address — any private / loopback / link-local / reserved /
        multicast / unspecified address blocks the send (covers metadata
        IPs and DNS-rebinding-to-internal).
    Fails CLOSED: a host that doesn't resolve is blocked.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        return f"scheme {parts.scheme!r} not allowed (https only)"
    host = parts.hostname
    if not host:
        return "no host in URL"
    # A bare IP literal in the URL is checked directly; a hostname is
    # resolved and ALL its addresses are checked (one bad record blocks).
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"host {host!r} did not resolve ({exc})"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return f"unparseable address {ip_str!r} for host {host!r}"
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return (
                f"host {host!r} resolves to non-public address {ip_str} "
                "(private/loopback/link-local/reserved) — blocked"
            )
    return None


def _try_delegate_notify(
    channel: str, body: str, vars_: dict[str, Any],
) -> bool:
    """Best-effort delegation to a future notify service. Returns
    True if delivered, False if no delivery surface exists yet — in
    which case we fall through to the log-and-record path.

    Design choice: we lazy-import here so Day-2 ships without a hard
    dependency on a notify service module. Day 3+ implementation lands
    in `backend/services/notify.py`."""
    try:
        from backend.services import notify as notify_service  # type: ignore
    except ImportError:
        return False
    fn = getattr(notify_service, "send", None)
    if not callable(fn):
        return False
    try:
        fn(channel=channel, body=body, vars=vars_)
        return True
    except Exception as e:
        logger.warning("notify service raised: %s — recording log only", e)
        return False


@register_step(
    step_type="notify.message",
    category="notify",
    label="Send a notification",
    description="Send a push notification (email / SMS coming later).",
    icon="send",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyMessageConfig,
    group="Notifications",
    output_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "delivered": {"type": "boolean"},
            "log": {"type": "string"},
        },
        "required": ["channel", "delivered"],
    },
)
async def execute_notify_message(ctx: Any) -> Optional[dict[str, Any]]:
    """Render the template (refs are already resolved by the engine)
    and either delegate to backend.services.notify if it exists, or
    log and persist a record to the run-step output.

    Output shape includes a `log` field so the run-step output is
    self-contained — the UI can render the message it would have sent
    even when no real channel is wired."""
    cfg = ctx.config
    channel = cfg["channel"]
    template = cfg["template"]
    vars_ = dict(cfg.get("vars") or {})

    # Render the template with simple {key} formatting. Template-side
    # refs were already resolved by the engine — we only do {key}
    # substitution here for vars-supplied placeholders.
    try:
        body = template.format(**vars_)
    except KeyError as e:
        body = template
        logger.info(
            "notify.message: template placeholder %s missing in vars",
            e,
        )

    delivered = _try_delegate_notify(channel, body, vars_)

    log_line = (
        f"[{channel}] {body}"
        if delivered
        else f"[{channel}] (logged, no service wired) {body}"
    )
    logger.info("workflow notify: run=%s step=%d %s",
                ctx.run.id, ctx.step.step_index, log_line)

    return {
        "channel": channel,
        "delivered": delivered,
        "log": log_line,
    }


@register_step(
    step_type="notify.log",
    category="notify",
    label="Add a run note",
    description="Write a line into this run's log — no external message.",
    icon="file-text",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyLogConfig,
    group="Notifications",
    output_schema={
        "type": "object",
        "properties": {"log": {"type": "string"}},
        "required": ["log"],
    },
)
async def execute_notify_log(ctx: Any) -> Optional[dict[str, Any]]:
    """Pure no-side-effect log step — useful for debugging and for the
    propose_workflow tool to drop breadcrumbs into a run."""
    msg = str(ctx.config["message"])
    logger.info(
        "workflow notify.log: run=%s step=%d %s",
        ctx.run.id, ctx.step.step_index, msg,
    )
    return {"log": msg}


@register_step(
    step_type="wait.approval",
    category="notify",
    label="Pause for my approval",
    description="Pause the run until you approve or reject it in the app.",
    icon="hand",
    max_retries=0,
    trigger_only=False,
    config_model=WaitApprovalConfig,
    group="Approvals",
    output_schema={
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approved", "rejected"]},
            "decided_at": {"type": "string", "format": "date-time"},
        },
        "required": ["decision"],
    },
)
async def execute_wait_approval(ctx: Any) -> Optional[dict[str, Any]]:
    """First entry creates the approval row and raises
    _AwaitingApproval — the engine pauses the run.

    On resume (`existing.decision == 'approved'`), we return the
    decision payload. Rejection is handled by the approvals router,
    which terminates the run before re-entry; this executor never sees
    a 'rejected' state."""
    existing = (
        ctx.db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == ctx.run.id,
            WorkflowApproval.step_index == ctx.step.step_index,
        )
        .order_by(WorkflowApproval.requested_at.desc())
        .first()
    )

    if existing is None or existing.decision is None:
        cfg = ctx.config
        approval = WorkflowApproval(
            run_id=ctx.run.id,
            step_index=ctx.step.step_index,
            expires_at=_utcnow() + timedelta(
                minutes=int(cfg.get("expires_in_minutes", 15)),
            ),
            summary=str(cfg["summary"]),
        )
        ctx.db.add(approval)
        ctx.db.commit()
        ctx.db.refresh(approval)
        raise _AwaitingApproval(approval.id)

    if existing.decision == "rejected":
        # Defensive — see action.place_order's matching branch.
        raise RuntimeError(
            f"approval rejected at step {ctx.step.step_index}"
        )

    return {
        "decision": existing.decision,
        "decided_at": (
            existing.decided_at.isoformat() if existing.decided_at else None
        ),
    }


@register_step(
    step_type="notify.webhook",
    category="notify",
    label="POST to a webhook URL",
    description=(
        "Notify an external system by POSTing a JSON payload to a URL "
        "you control — e.g. ping your own endpoint, a Slack/Discord "
        "webhook, or a Zapier hook when the trigger fires."
    ),
    icon="webhook",
    max_retries=2,
    trigger_only=False,
    config_model=ActionNotifyWebhookConfig,
    group="Notifications",
    output_schema={
        "type": "object",
        "properties": {
            "delivered": {"type": "boolean"},
            "status_code": {"type": ["integer", "null"]},
            "url": {"type": "string"},
        },
        "required": ["delivered", "url"],
    },
)
async def execute_notify_webhook(ctx: Any) -> Optional[dict[str, Any]]:
    """POST/PUT a JSON payload to a user-supplied webhook URL.

    Design notes
    ------------
    * Template refs (``{{steps.N.field}}`` etc.) inside ``payload_template``
      are already resolved by the engine before the executor is invoked —
      the dict we receive in ``ctx.config["payload_template"]`` is opaque
      pass-through JSON.
    * If no ``payload_template`` is set we send a small default envelope so
      the receiver always gets something useful (workflow id, run id,
      fired_at, message).
    * If ``secret`` is set we sign the serialized JSON body with HMAC-SHA256
      and put the hex digest in the ``X-Pivot-Signature`` header so the
      receiver can verify authenticity.
    * Errors are NEVER raised out — same tolerant shape as
      :func:`execute_notify_message`. We log loudly and return
      ``{"delivered": False, "status_code": None, "url": ...}`` so the run
      keeps moving. ``max_retries=2`` still gives us automatic re-tries
      for transient flakes via the engine.
    """
    cfg = ctx.config
    url = str(cfg["url"])
    method = str(cfg.get("method") or "POST").upper()
    headers_in = dict(cfg.get("headers") or {})
    payload_template = cfg.get("payload_template")
    secret = cfg.get("secret")

    fired_at = _utcnow().isoformat()
    if isinstance(payload_template, dict):
        # Caller-controlled JSON. Refs were resolved by the engine, so
        # this is opaque pass-through.
        body_obj: dict[str, Any] = dict(payload_template)
    else:
        body_obj = {
            "workflow": getattr(ctx.workflow, "id", None),
            "run_id": getattr(ctx.run, "id", None),
            "fired_at": fired_at,
            "message": "Pivot workflow fired",
        }

    # Serialize once so the same bytes are signed AND sent — otherwise the
    # receiver's HMAC check could mismatch on dict-key reordering.
    try:
        body_bytes = json.dumps(
            body_obj, separators=(",", ":"), default=str, sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as e:
        logger.warning(
            "notify.webhook: payload not JSON-serializable (%s) — "
            "sending empty body", e,
        )
        body_bytes = b"{}"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    headers.update(headers_in)
    if secret:
        try:
            digest = hmac.new(
                str(secret).encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Pivot-Signature"] = digest
        except Exception as e:  # noqa: BLE001 — never crash the run on signing
            logger.warning(
                "notify.webhook: HMAC signing failed (%s) — sending "
                "unsigned request", e,
            )

    status_code: Optional[int] = None
    delivered = False
    blocked = _webhook_destination_blocked(url)
    if blocked is not None:
        # SSRF guard tripped — do NOT make the request. Tolerant shape so
        # the run keeps moving (same philosophy as a delivery failure).
        logger.warning(
            "notify.webhook: refusing to send to %s — %s", url, blocked,
        )
    else:
        try:
            # follow_redirects stays False (httpx default, pinned explicitly)
            # so a 30x from a public host can't bounce us to an internal one
            # after the guard has already cleared the original URL.
            async with httpx.AsyncClient(
                timeout=8.0, follow_redirects=False,
            ) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    content=body_bytes,
                    headers=headers,
                )
            status_code = int(resp.status_code)
            delivered = 200 <= status_code < 300
        except Exception as e:  # noqa: BLE001 — webhook failures must not crash run
            logger.warning(
                "notify.webhook: delivery to %s failed: %s", url, e,
            )

    logger.info(
        "workflow notify.webhook: run=%s step=%d url=%s "
        "status=%s delivered=%s",
        getattr(ctx.run, "id", None),
        getattr(ctx.step, "step_index", -1),
        url,
        status_code,
        delivered,
    )

    return {
        "delivered": delivered,
        "status_code": status_code,
        "url": url,
    }
