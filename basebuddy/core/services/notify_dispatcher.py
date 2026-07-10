"""
Deliver notifications: Telegram (multipart photo/video), email, Pushover, SMS, webhooks.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import smtplib
import ssl
import threading
import urllib.parse
import urllib.request
import uuid
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _cfg():
    from basebuddy.modules import config
    return config


def _public_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    cfg = _cfg()
    base = (getattr(cfg, "NOTIFY_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if path.startswith("http"):
        return path
    p = path if path.startswith("/") else f"/{path}"
    if base:
        return f"{base}{p}"
    return p


class NotifyDispatcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def send(
        self,
        channels: set,
        message: str,
        *,
        snapshot_fs: Optional[str] = None,
        snapshot_url: Optional[str] = None,
        clip_fs: Optional[str] = None,
        clip_url: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        include_snapshot: bool = True,
        include_clip: bool = False,
    ) -> None:
        if not channels:
            return
        payload = payload or {}
        pub_snap = _public_url(snapshot_url)
        pub_clip = _public_url(clip_url)

        if "webhook" in channels:
            self._webhook(payload, message, pub_snap, pub_clip)
        if "telegram" in channels:
            self._telegram(message, snapshot_fs, clip_fs if include_clip else None, include_snapshot)
        if "email" in channels:
            self._email(message, snapshot_fs, clip_fs if include_clip else None, include_snapshot)
        if "pushover" in channels:
            self._pushover(message, pub_snap or pub_clip)
        if "sms" in channels:
            link = pub_clip or pub_snap or ""
            self._sms(f"{message}" + (f" {link}" if link else ""))

    def _webhook(self, payload: dict, message: str, snap_url: Optional[str], clip_url: Optional[str]) -> None:
        cfg = _cfg()
        body = {**payload, "message": message, "snapshot_url": snap_url, "clip_url": clip_url}
        url = payload.get("webhook_url") or getattr(cfg, "NOTIFY_WEBHOOK_URL", "")
        if url:
            self._post_json(url, body)

    def _telegram(
        self,
        message: str,
        snapshot_fs: Optional[str],
        clip_fs: Optional[str],
        include_snapshot: bool,
    ) -> None:
        cfg = _cfg()
        token = cfg.env_live("TELEGRAM_BOT_TOKEN", "")
        chat_id = cfg.env_live("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        try:
            import requests
        except ImportError:
            logger.warning("requests required for Telegram file upload")
            return
        base = f"https://api.telegram.org/bot{token}"
        try:
            if clip_fs and __import__("os").path.isfile(clip_fs):
                with open(clip_fs, "rb") as fh:
                    requests.post(
                        f"{base}/sendVideo",
                        data={"chat_id": chat_id, "caption": message[:1024], "supports_streaming": "true"},
                        files={"video": fh},
                        timeout=60,
                    )
                return
            if include_snapshot and snapshot_fs and __import__("os").path.isfile(snapshot_fs):
                with open(snapshot_fs, "rb") as fh:
                    requests.post(
                        f"{base}/sendPhoto",
                        data={"chat_id": chat_id, "caption": message[:1024]},
                        files={"photo": fh},
                        timeout=30,
                    )
                return
            requests.post(
                f"{base}/sendMessage",
                data={"chat_id": chat_id, "text": message[:4096]},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("Telegram notify failed: %s", exc)

    def _email(
        self,
        message: str,
        snapshot_fs: Optional[str],
        clip_fs: Optional[str],
        include_snapshot: bool,
    ) -> None:
        cfg = _cfg()
        host = getattr(cfg, "SMTP_HOST", "")
        if not host:
            return
        port = int(getattr(cfg, "SMTP_PORT", 587) or 587)
        user = getattr(cfg, "SMTP_USER", "")
        password = getattr(cfg, "SMTP_PASSWORD", "")
        from_addr = getattr(cfg, "SMTP_FROM", user)
        to_raw = getattr(cfg, "SMTP_TO", "")
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
        if not to_addrs:
            return
        use_tls = getattr(cfg, "SMTP_USE_TLS", True)
        msg = MIMEMultipart()
        msg["Subject"] = message[:120]
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(message, "plain"))
        if include_snapshot and snapshot_fs:
            try:
                with open(snapshot_fs, "rb") as fh:
                    img = MIMEImage(fh.read(), name=__import__("os").path.basename(snapshot_fs))
                    img.add_header("Content-Disposition", "attachment", filename="detection.jpg")
                    msg.attach(img)
            except OSError as exc:
                logger.warning("Email snapshot attach failed: %s", exc)
        if clip_fs:
            try:
                with open(clip_fs, "rb") as fh:
                    part = MIMEApplication(fh.read(), Name=__import__("os").path.basename(clip_fs))
                    part["Content-Disposition"] = f'attachment; filename="{__import__("os").path.basename(clip_fs)}"'
                    msg.attach(part)
            except OSError as exc:
                logger.warning("Email clip attach failed: %s", exc)
        try:
            if use_tls:
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    if user:
                        smtp.login(user, password)
                    smtp.sendmail(from_addr, to_addrs, msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    if user:
                        smtp.login(user, password)
                    smtp.sendmail(from_addr, to_addrs, msg.as_string())
        except Exception as exc:
            logger.warning("Email notify failed: %s", exc)

    def _pushover(self, message: str, link: Optional[str]) -> None:
        cfg = _cfg()
        try:
            fields = {
                "token": cfg.PUSHOVER_API_TOKEN,
                "user": cfg.PUSHOVER_USER_KEY,
                "message": message[:1024],
            }
            if link:
                fields["url"] = link
                fields["url_title"] = "View"
            data = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data, method="POST")
            urllib.request.urlopen(req, timeout=10)
        except Exception as exc:
            logger.warning("Pushover notify failed: %s", exc)

    def _sms(self, message: str) -> None:
        cfg = _cfg()
        sid = getattr(cfg, "TWILIO_ACCOUNT_SID", "")
        token = getattr(cfg, "TWILIO_AUTH_TOKEN", "")
        from_num = getattr(cfg, "TWILIO_FROM_NUMBER", "")
        to_num = getattr(cfg, "TWILIO_TO_NUMBER", "")
        if not all([sid, token, from_num, to_num]):
            return
        try:
            import requests
            requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={"From": from_num, "To": to_num, "Body": message[:1500]},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("SMS notify failed: %s", exc)

    @staticmethod
    def _post_json(url: str, payload: dict) -> None:
        try:
            data = json.dumps(payload, default=str).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as exc:
            logger.warning("Webhook failed: %s", exc)


_dispatcher: Optional[NotifyDispatcher] = None


def get_notify_dispatcher() -> NotifyDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = NotifyDispatcher()
    return _dispatcher
