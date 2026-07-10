"""Build and publish automated plant blog / social posts."""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DESTINATION_TYPES = ("webhook", "mastodon", "wordpress", "telegram", "bluesky", "mqtt")


def publish_channel(channel_id: str, *, trigger: str = "scheduled") -> Dict[str, Any]:
    from basebuddy.plugins.plant_health.blogger_config import get_channel
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.db import save_blogger_post

    channel = get_channel(channel_id)
    if not channel:
        return {"ok": False, "error": "Channel not found"}

    monitor = get_monitor(channel.get("monitor_id") or "")
    if not monitor:
        return {"ok": False, "error": "Linked plant monitor not found"}

    try:
        payload = _assemble_post(channel, monitor)
        result = _dispatch_destination(channel, payload)
        save_blogger_post(
            channel_id,
            channel.get("monitor_id") or "",
            payload.get("text") or "",
            payload.get("title") or "",
            payload.get("image_path"),
            ok=result.get("ok", False),
            error=result.get("error"),
            destination=channel.get("destination", {}).get("type"),
            trigger=trigger,
        )
        return {**result, "text": payload.get("text"), "title": payload.get("title"), "image_path": payload.get("image_path")}
    except Exception as exc:
        logger.exception("Plant blogger publish failed")
        save_blogger_post(
            channel_id,
            channel.get("monitor_id") or "",
            "",
            "",
            None,
            ok=False,
            error=str(exc),
            destination=(channel.get("destination") or {}).get("type"),
            trigger=trigger,
        )
        return {"ok": False, "error": str(exc)}


def preview_channel(channel_id: str) -> Dict[str, Any]:
    from basebuddy.plugins.plant_health.blogger_config import get_channel
    from basebuddy.plugins.plant_health.config import get_monitor

    channel = get_channel(channel_id)
    if not channel:
        return {"ok": False, "error": "Channel not found"}
    monitor = get_monitor(channel.get("monitor_id") or "")
    if not monitor:
        return {"ok": False, "error": "Linked plant monitor not found"}
    payload = _assemble_post(channel, monitor, dry_run=True)
    return {
        "ok": True,
        "title": payload.get("title"),
        "text": payload.get("text"),
        "image_path": payload.get("image_path"),
        "context": payload.get("context"),
    }


def _assemble_post(channel: dict, monitor: dict, *, dry_run: bool = False) -> dict:
    from basebuddy.plugins.plant_health.db import list_analyses
    from basebuddy.plugins.plant_health.metrics import extract_metrics
    from basebuddy.plugins.plant_health.service import capture_frame

    content_opts = channel.get("content") or {}
    camera_id = int(monitor.get("camera_id", 0))

    frame, image_bytes, image_path = capture_frame(camera_id)
    if frame is None or not image_bytes:
        raise RuntimeError("No frame from camera — is it running on Camera Wall?")

    color_metrics = extract_metrics(frame, monitor) or {}

    vision_result = None
    if content_opts.get("run_vision_before_post") and not dry_run:
        from basebuddy.core.premium_hooks import get_plant_health_analyzer
        from basebuddy.plugins.plant_health.db import save_analysis

        analyzer = get_plant_health_analyzer()
        if analyzer.is_configured():
            vision_result = analyzer.analyze(
                image_bytes,
                species_hint=monitor.get("species_hint") or "",
                monitor=monitor,
            )
            save_analysis(
                monitor.get("id") or "",
                camera_id,
                vision_result,
                image_path=image_path,
                analyzer=getattr(analyzer, "name", "oss"),
                error=vision_result.get("error"),
            )

    if vision_result is None:
        history = list_analyses(monitor.get("id") or "", limit=1)
        if history:
            vision_result = history[0].get("result") or {}
            if history[0].get("health_score") is not None:
                vision_result = {**vision_result, "health_score": history[0]["health_score"]}

    ctx = _build_context(monitor, vision_result, color_metrics, image_path)
    title = _render_template(content_opts.get("title_template") or "{{monitor_name}} update", ctx)
    if content_opts.get("caption_template"):
        text = _render_template(content_opts["caption_template"], ctx)
    else:
        text = _build_caption(content_opts, ctx)

    return {
        "title": title,
        "text": text,
        "image_bytes": image_bytes if content_opts.get("include_image", True) else None,
        "image_path": image_path if content_opts.get("include_image", True) else None,
        "context": ctx,
    }


def _build_context(monitor: dict, vision: Optional[dict], color: dict, image_path: Optional[str]) -> dict:
    vision = vision or {}
    recs = vision.get("recommendations") or []
    return {
        "monitor_id": monitor.get("id") or "",
        "monitor_name": monitor.get("name") or "Plant",
        "species": monitor.get("species_hint") or "",
        "health_score": str(vision.get("health_score") if vision.get("health_score") is not None else ""),
        "summary": vision.get("summary") or "",
        "species_guess": vision.get("species_guess") or "",
        "greenness": f"{color.get('greenness', 0):.3f}" if color.get("greenness") is not None else "",
        "coverage": f"{(color.get('coverage') or 0) * 100:.1f}%" if color.get("coverage") is not None else "",
        "recommendations": "; ".join(str(r) for r in recs[:3]),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "image_path": image_path or "",
        "image_url": _public_url(image_path),
    }


def _build_caption(content_opts: dict, ctx: dict) -> str:
    parts: List[str] = []
    if content_opts.get("custom_intro"):
        parts.append(_render_template(content_opts["custom_intro"], ctx))
    parts.append(f"🌱 {ctx['monitor_name']}")
    if content_opts.get("include_species") and ctx.get("species"):
        parts.append(ctx["species"])
    if content_opts.get("include_health_score") and ctx.get("health_score"):
        parts.append(f"Health score: {ctx['health_score']}/100")
    if content_opts.get("include_summary") and ctx.get("summary"):
        parts.append(ctx["summary"])
    if content_opts.get("include_greenness") and ctx.get("greenness"):
        parts.append(f"Greenness index: {ctx['greenness']}")
    if content_opts.get("include_coverage") and ctx.get("coverage"):
        parts.append(f"Plant coverage: {ctx['coverage']}")
    if content_opts.get("include_recommendations") and ctx.get("recommendations"):
        parts.append(f"Tips: {ctx['recommendations']}")
    if content_opts.get("hashtags"):
        parts.append(content_opts["hashtags"])
    if content_opts.get("custom_outro"):
        parts.append(_render_template(content_opts["custom_outro"], ctx))
    return "\n\n".join(p for p in parts if p)


def _render_template(template: str, ctx: dict) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _public_url(path: Optional[str]) -> str:
    if not path:
        return ""
    from basebuddy.modules.config import NOTIFY_PUBLIC_BASE_URL
    base = (NOTIFY_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if path.startswith("http"):
        return path
    p = path if path.startswith("/") else f"/{path}"
    return f"{base}{p}" if base else p


def _dispatch_destination(channel: dict, payload: dict) -> dict:
    dest = channel.get("destination") or {}
    dtype = (dest.get("type") or "webhook").lower()
    cfg = dest.get("config") or {}

    if dtype == "webhook":
        return _publish_webhook(cfg, payload)
    if dtype == "mastodon":
        return _publish_mastodon(cfg, payload)
    if dtype == "wordpress":
        return _publish_wordpress(cfg, payload)
    if dtype == "telegram":
        return _publish_telegram(cfg, payload)
    if dtype == "bluesky":
        return _publish_bluesky(cfg, payload)
    if dtype == "mqtt":
        return _publish_mqtt(cfg, payload, channel)
    return {"ok": False, "error": f"Unknown destination: {dtype}"}


def _publish_webhook(cfg: dict, payload: dict) -> dict:
    url = (cfg.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "Webhook URL required"}

    mode = (cfg.get("mode") or "json").lower()
    method = (cfg.get("method") or "POST").upper()

    if mode == "multipart" and payload.get("image_bytes"):
        return _post_multipart(url, payload, cfg)

    body = {
        "title": payload.get("title"),
        "text": payload.get("text"),
        "caption": payload.get("text"),
        "image_url": _public_url(payload.get("image_path")),
        "image_path": payload.get("image_path"),
        **(cfg.get("extra_fields") or {}),
    }
    if cfg.get("include_image_base64") and payload.get("image_bytes"):
        body["image_base64"] = base64.b64encode(payload["image_bytes"]).decode("ascii")

    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.get("headers") and isinstance(cfg["headers"], dict):
        headers.update({str(k): str(v) for k, v in cfg["headers"].items()})
    req = urllib.request.Request(url, data=data if method != "GET" else None, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return {"ok": True, "status": resp.status, "type": "webhook"}


def _post_multipart(url: str, payload: dict, cfg: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests package required for multipart webhook"}

    boundary = uuid.uuid4().hex
    fields = {
        "title": payload.get("title") or "",
        "text": payload.get("text") or "",
        "caption": payload.get("text") or "",
    }
    fields.update(cfg.get("extra_fields") or {})
    files = {"image": ("plant.jpg", payload["image_bytes"], "image/jpeg")}
    headers = dict(cfg.get("headers") or {})
    r = requests.post(url, data=fields, files=files, headers=headers, timeout=60)
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "status": r.status_code, "type": "webhook"}


def _publish_mastodon(cfg: dict, payload: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests package required for Mastodon"}

    instance = (cfg.get("instance") or "").strip().rstrip("/")
    token = (cfg.get("access_token") or "").strip()
    if not instance or not token:
        return {"ok": False, "error": "Mastodon instance URL and access token required"}

    headers = {"Authorization": f"Bearer {token}"}
    media_ids: List[str] = []
    if payload.get("image_bytes"):
        files = {"file": ("plant.jpg", payload["image_bytes"], "image/jpeg")}
        data = {"description": payload.get("title") or payload.get("text", "")[:420]}
        r = requests.post(f"{instance}/api/v1/media", headers=headers, files=files, data=data, timeout=60)
        if r.status_code >= 400:
            return {"ok": False, "error": f"Mastodon media upload failed: {r.text[:200]}"}
        media_ids.append(str(r.json().get("id")))

    status_data = {"status": payload.get("text") or ""}
    for i, mid in enumerate(media_ids):
        status_data[f"media_ids[{i}]"] = mid
    if cfg.get("visibility"):
        status_data["visibility"] = cfg["visibility"]

    r = requests.post(f"{instance}/api/v1/statuses", headers=headers, data=status_data, timeout=30)
    if r.status_code >= 400:
        return {"ok": False, "error": f"Mastodon post failed: {r.text[:200]}"}
    return {"ok": True, "type": "mastodon", "status_id": r.json().get("id")}


def _publish_wordpress(cfg: dict, payload: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests package required for WordPress"}

    site = (cfg.get("site_url") or cfg.get("url") or "").strip().rstrip("/")
    user = (cfg.get("username") or "").strip()
    app_password = (cfg.get("app_password") or cfg.get("password") or "").strip()
    if not site or not user or not app_password:
        return {"ok": False, "error": "WordPress site URL, username, and app password required"}

    auth = (user, app_password.replace(" ", ""))
    featured_media = None
    if payload.get("image_bytes"):
        files = {"file": ("plant.jpg", payload["image_bytes"], "image/jpeg")}
        r = requests.post(
            f"{site}/wp-json/wp/v2/media",
            auth=auth,
            files=files,
            data={"title": payload.get("title") or "Plant update", "caption": payload.get("text", "")[:500]},
            timeout=60,
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"WordPress media upload failed: {r.text[:200]}"}
        featured_media = r.json().get("id")

    post_body = {
        "title": payload.get("title") or "Plant update",
        "content": f"<p>{_html_escape(payload.get('text') or '')}</p>",
        "status": cfg.get("status") or "publish",
    }
    if featured_media:
        post_body["featured_media"] = featured_media

    r = requests.post(f"{site}/wp-json/wp/v2/posts", auth=auth, json=post_body, timeout=30)
    if r.status_code >= 400:
        return {"ok": False, "error": f"WordPress post failed: {r.text[:200]}"}
    return {"ok": True, "type": "wordpress", "post_id": r.json().get("id"), "link": r.json().get("link")}


def _publish_telegram(cfg: dict, payload: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests package required for Telegram"}

    from basebuddy.modules.config import env_live
    token = (cfg.get("bot_token") or env_live("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = (cfg.get("chat_id") or env_live("TELEGRAM_CHAT_ID", "") or "").strip()
    if not token or not chat_id:
        return {"ok": False, "error": "Telegram bot token and chat ID required (Integrations or channel config)"}

    base = f"https://api.telegram.org/bot{token}"
    caption = (payload.get("text") or "")[:1024]
    if payload.get("image_bytes"):
        files = {"photo": ("plant.jpg", payload["image_bytes"], "image/jpeg")}
        r = requests.post(f"{base}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files=files, timeout=30)
    else:
        r = requests.post(f"{base}/sendMessage", data={"chat_id": chat_id, "text": caption}, timeout=30)
    if r.status_code >= 400 or not r.json().get("ok"):
        return {"ok": False, "error": r.text[:200]}
    return {"ok": True, "type": "telegram"}


def _publish_bluesky(cfg: dict, payload: dict) -> dict:
    """Bluesky AT Protocol — session + post with optional embed image."""
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests package required for Bluesky"}

    handle = (cfg.get("handle") or "").strip()
    app_password = (cfg.get("app_password") or "").strip()
    service = (cfg.get("service") or "https://bsky.social").strip().rstrip("/")
    if not handle or not app_password:
        return {"ok": False, "error": "Bluesky handle and app password required"}

    r = requests.post(
        f"{service}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=30,
    )
    if r.status_code >= 400:
        return {"ok": False, "error": f"Bluesky login failed: {r.text[:200]}"}
    session = r.json()
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}

    embed = None
    if payload.get("image_bytes"):
        blob = _bluesky_upload_blob(service, headers, payload["image_bytes"])
        if blob:
            embed = {"$type": "app.bsky.embed.images", "images": [{"alt": payload.get("title") or "Plant", "image": blob}]}

    record = {
        "$type": "app.bsky.feed.post",
        "text": payload.get("text") or "",
        "createdAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    if embed:
        record["embed"] = embed

    r = requests.post(
        f"{service}/xrpc/com.atproto.repo.createRecord",
        headers=headers,
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
        timeout=30,
    )
    if r.status_code >= 400:
        return {"ok": False, "error": f"Bluesky post failed: {r.text[:200]}"}
    return {"ok": True, "type": "bluesky", "uri": r.json().get("uri")}


def _bluesky_upload_blob(service: str, headers: dict, image_bytes: bytes) -> Optional[dict]:
    import requests

    r = requests.post(
        f"{service}/xrpc/com.atproto.repo.uploadBlob",
        headers={**headers, "Content-Type": "image/jpeg"},
        data=image_bytes,
        timeout=60,
    )
    if r.status_code >= 400:
        return None
    return r.json().get("blob")


def _publish_mqtt(cfg: dict, payload: dict, channel: dict) -> dict:
    topic = (cfg.get("topic") or "").strip()
    if not topic:
        return {"ok": False, "error": "MQTT topic required"}
    body = json.dumps({
        "event": "plant_blog_post",
        "channel_id": channel.get("id"),
        "monitor_id": channel.get("monitor_id"),
        "title": payload.get("title"),
        "text": payload.get("text"),
        "image_path": payload.get("image_path"),
        "image_url": _public_url(payload.get("image_path")),
        "ts": time.time(),
    })
    from basebuddy.core.services.mqtt_publisher import publish_raw
    if not publish_raw(topic, body, qos=int(cfg.get("qos", 0))):
        return {"ok": False, "error": "MQTT publish failed (enable MQTT in Integrations)"}
    return {"ok": True, "type": "mqtt", "topic": topic}


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
