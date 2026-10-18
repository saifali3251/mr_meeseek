"""IMAP listener and in-memory store for Jira evaluator login OTP verification codes.

Allows hackathon evaluators and judges to log in directly to Jira without getting
blocked by Atlassian's 2FA / email verification wall. Runs an in-process IMAP
poller against the evaluator's Gmail (using an App Password) to fetch the 6-digit
code in real-time (<3s latency) and exposes it via GET /ops/jira/otp.
"""

from __future__ import annotations

from datetime import datetime
import email
from email.header import decode_header
import email.utils
import imaplib
import logging
import re
import threading
import time
from typing import Optional

log = logging.getLogger("holodeck.otp")


def _decode_header(val: str) -> str:
    if not val:
        return ""
    try:
        parts = decode_header(val)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                try:
                    out.append(text.decode(enc or "utf-8", errors="replace"))
                except Exception:
                    out.append(text.decode("latin1", errors="replace"))
            else:
                out.append(str(text))
        return " ".join(out)
    except Exception:
        return str(val)


def _extract_body(msg) -> str:
    texts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            if "attachment" in cdispo:
                continue
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        texts.append(payload.decode("utf-8", errors="replace"))
                    except Exception:
                        texts.append(payload.decode("latin1", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                texts.append(payload.decode("utf-8", errors="replace"))
            except Exception:
                texts.append(payload.decode("latin1", errors="replace"))
    return "\n".join(texts)


class JiraOtpService:
    """Manages extraction and in-memory caching of Atlassian login verification codes."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.latest_otp: Optional[dict] = None
        self.history: list[dict] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        pw = getattr(self.cfg, "jira_evaluator_imap_password", "")
        user = (
            getattr(self.cfg, "jira_evaluator_imap_user", "")
            or getattr(self.cfg, "jira_evaluator_email", "")
        )
        if not pw or not user:
            log.info(
                "Jira evaluator IMAP credentials not configured (HOLODECK_JIRA_EVALUATOR_IMAP_PASSWORD empty). "
                "Manual /ops/jira/otp endpoint remains active."
            )
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="jira-otp-imap-poller",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Jira evaluator IMAP poller started for %s (server=%s, interval=%ss)",
            user,
            getattr(self.cfg, "jira_evaluator_imap_server", "imap.gmail.com"),
            getattr(self.cfg, "jira_evaluator_imap_interval_s", 3),
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        log.info("Jira evaluator IMAP poller stopped")

    def set_otp(
        self,
        code: str,
        timestamp: Optional[float] = None,
        subject: str = "Atlassian Verification Code",
        from_addr: str = "noreply@id.atlassian.com",
    ) -> dict:
        code_clean = "".join(c for c in str(code) if c.isdigit())[:6]
        rec_time = float(timestamp) if timestamp is not None else time.time()
        rec_formatted = datetime.fromtimestamp(rec_time).strftime("%I:%M:%S %p")

        item = {
            "code": code_clean,
            "received_at": rec_time,
            "received_at_formatted": rec_formatted,
            "subject": subject,
            "from": from_addr,
        }
        with self.lock:
            self.latest_otp = item
            # Keep up to 5 historical entries, avoiding consecutive identical duplicates
            if not self.history or self.history[0]["code"] != code_clean:
                self.history = [item] + self.history[:4]
        log.info("Received Jira OTP: %s (at %s)", code_clean, rec_formatted)
        return item

    def get_state(self) -> dict:
        with self.lock:
            latest = dict(self.latest_otp) if self.latest_otp else None
            hist = [dict(h) for h in self.history]

        if latest:
            latest["age_seconds"] = max(0, int(time.time() - latest["received_at"]))

        return {
            "evaluator_email": getattr(self.cfg, "jira_evaluator_email", "") or "",
            "evaluator_password": getattr(self.cfg, "jira_evaluator_password", "") or "",
            "jira_url": getattr(self.cfg, "jira_base_url", "") or "",
            "project": getattr(self.cfg, "jira_project", "") or "FSA",
            "latest_otp": latest,
            "history": hist,
            "imap_active": bool(self._thread and self._thread.is_alive()),
        }

    def _poll_loop(self) -> None:
        interval = max(2, getattr(self.cfg, "jira_evaluator_imap_interval_s", 3))
        while not self._stop_event.is_set():
            try:
                self._check_inbox()
            except Exception as e:
                log.debug("Error in IMAP inbox check: %s", e)
            self._stop_event.wait(interval)

    def _check_inbox(self) -> None:
        user = (
            getattr(self.cfg, "jira_evaluator_imap_user", "")
            or getattr(self.cfg, "jira_evaluator_email", "")
        )
        pw = getattr(self.cfg, "jira_evaluator_imap_password", "")
        server = getattr(self.cfg, "jira_evaluator_imap_server", "imap.gmail.com")

        if not user or not pw:
            return

        client = None
        try:
            client = imaplib.IMAP4_SSL(server, 993, timeout=10)
            client.login(user, pw)
            client.select("INBOX", readonly=True)

            status, data = client.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                return

            msg_ids = data[0].split()
            # Inspect last 5 messages, newest first
            for mid in reversed(msg_ids[-5:]):
                status, msg_data = client.fetch(mid, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue

                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue

                msg = email.message_from_bytes(raw)
                subject = _decode_header(msg.get("Subject", ""))
                from_hdr = _decode_header(msg.get("From", ""))
                date_hdr = msg.get("Date", "")

                is_candidate = (
                    "atlassian" in from_hdr.lower()
                    or "atlassian" in subject.lower()
                    or "verification" in subject.lower()
                    or "verification" in from_hdr.lower()
                    or "verify" in subject.lower()
                    or "security code" in subject.lower()
                )
                if not is_candidate:
                    continue

                body = _extract_body(msg)
                m = re.search(r"\b([0-9]{6})\b", subject) or re.search(r"\b([0-9]{6})\b", body)
                if m:
                    code = m.group(1)
                    with self.lock:
                        if self.latest_otp and self.latest_otp.get("code") == code:
                            break

                    rec_time = time.time()
                    try:
                        parsed_date = email.utils.parsedate_to_datetime(date_hdr)
                        rec_time = parsed_date.timestamp()
                    except Exception:
                        pass

                    self.set_otp(
                        code=code,
                        timestamp=rec_time,
                        subject=subject[:100],
                        from_addr=from_hdr[:100],
                    )
                    break
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                try:
                    client.logout()
                except Exception:
                    pass

