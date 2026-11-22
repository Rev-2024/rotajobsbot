"""Thin client for the WhatsApp Cloud API: signature check, payload building, sending, parsing."""
import hashlib
import hmac
import logging

import httpx

from .config import settings
from .flows import clip
from .messages import Incoming, Out

log = logging.getLogger("whatsapp")


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header. Without an app secret we only accept in DRY_RUN."""
    if not settings.wa_app_secret:
        return settings.dry_run
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.wa_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def to_payload(out: Out) -> dict:
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": out.to}

    if out.kind == "text":
        return {**base, "type": "text", "text": {"body": clip(out.body, 4096), "preview_url": True}}

    if out.kind == "buttons":
        return {
            **base,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": clip(out.body, 1024)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": bid[:256], "title": clip(title, 20)}}
                        for bid, title in out.buttons[:3]  # WhatsApp allows at most 3
                    ]
                },
            },
        }

    if out.kind == "list":
        rows = [
            {
                "id": r["id"][:200],
                "title": clip(r["title"], 24),
                **({"description": clip(r["description"], 72)} if r.get("description") else {}),
            }
            for r in out.rows[:10]  # WhatsApp allows at most 10 rows
        ]
        return {
            **base,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": clip(out.body, 1024)},
                "action": {"button": clip(out.list_button, 20), "sections": [{"title": "Options", "rows": rows}]},
            },
        }

    raise ValueError(f"unknown message kind: {out.kind}")


def send(out: Out) -> bool:
    """Send one message. Never raises: a failed send must not break the rest of the batch."""
    if settings.dry_run or not settings.wa_token:
        log.info("[dry run] to %s: %s", out.to, out.body)
        return True
    url = f"https://graph.facebook.com/{settings.graph_version}/{settings.wa_phone_number_id}/messages"
    try:
        r = httpx.post(
            url,
            json=to_payload(out),
            headers={"Authorization": f"Bearer {settings.wa_token}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        log.warning("send to %s failed: %s", out.to, exc)
        return False
    if r.status_code >= 400:
        # Typical cause: a free-form message outside the 24-hour customer service window.
        log.warning("send to %s rejected (%s): %s", out.to, r.status_code, r.text[:300])
        return False
    return True


def parse_webhook(body: dict) -> list[tuple[str, Incoming]]:
    """Extract (message_id, Incoming) pairs. Delivery-status callbacks have no messages and are skipped."""
    items: list[tuple[str, Incoming]] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                phone = m.get("from", "")
                msg_id = m.get("id", "")
                if not phone or not msg_id:
                    continue
                text, reply_id = "", ""
                if m.get("type") == "text":
                    text = m.get("text", {}).get("body", "")
                elif m.get("type") == "interactive":
                    inter = m.get("interactive", {})
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    reply_id = reply.get("id", "")
                    text = reply.get("title", "")
                # images, audio, etc. arrive as empty text and fall through to the menu
                items.append((msg_id, Incoming(phone=phone, text=text, reply_id=reply_id, name=names.get(phone, ""))))
    return items
