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

# Expire OTP after 5 minutes (300s) so the UI resets to "Waiting for login..."
OTP_TTL_SECONDS = 300


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


def _clean_html(raw: str) -> str:
    """Strip <style>/<script>/<head> blocks and HTML tags so CSS hex colors
    (like #0052CC or #172B4D) and HTML attributes never masquerade as 6-char OTPs."""
    text = re.sub(r"<(style|script|head)\b[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"#[0-9A-Fa-f]{6}\b", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_otp_code(subject: str, body: str) -> Optional[str]:
    """Extract 6-character alphanumeric Atlassian verification code from email subject or body."""
    # 1. Subject match with digit: "Your verification code: PKRKB5" or "123456"
    # Exclude Jira ticket keys like FSA-15 or CPL-100
    subj_clean = re.sub(r"\b[A-Z]+-\d+\b", " ", subject)
    m_subj = re.search(r"\b([A-Z0-9]{6})\b", subj_clean)
    if m_subj and any(c.isdigit() for c in m_subj.group(1)):
        return m_subj.group(1).upper()

    combined = f"{subj_clean} {_clean_html(body)}"

    # 2. Targeted contextual patterns (Atlassian's standard email phrases)
    context_patterns = [
        r"(?:enter the following code|enter the code|following code|verification code|security code)[:\s]+([A-Za-z0-9]{6})\b",
        r"code requested[:\s]+([A-Za-z0-9\s]{6,15})",
        r"to access [^,]+, enter (?:the following )?code[:\s]+([A-Za-z0-9]{6})\b",
        r"(?:your )?(?:verification|security)?\s*code is[:\s]+([A-Za-z0-9]{6})\b",
    ]
    for pat in context_patterns:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            cand = "".join(c for c in m.group(1) if c.isalnum()).upper()
            if len(cand) == 6:
                return cand

    # 3. Spaced letters/digits like "P K R K B 5" (preview header in Atlassian emails)
    m_spaced = re.search(r"\b([A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9])\b", combined)
    if m_spaced:
        cand = "".join(c for c in m_spaced.group(1) if c.isalnum()).upper()
        if len(cand) == 6:
            return cand

    # 4. Standard 6-digit numeric OTP
    m_digits = re.search(r"\b([0-9]{6})\b", combined)
    if m_digits:
        return m_digits.group(1)

    # 5. Standalone 6-character alphanumeric tokens (must contain a digit; exclude CSS/HTML hex tokens)
    stop_words = {
        "ACCESS", "ACTION", "ATLASSIAN", "VERIFY", "SECURITY", "SYSTEM",
        "BROWSER", "DEVICE", "ENABLE", "UPDATE", "PLEASE", "CHROME",
        "SAFARI", "STATUS", "COOKIE", "DOMAIN", "NEARLY", "0052CC",
        "172B4D", "DEEBFF", "42526E", "F4F5F7", "FFFFFF", "000000"
    }
    tokens = re.findall(r"\b([A-Z0-9]{6})\b", combined)
    for tok in tokens:
        if tok not in stop_words and any(c.isdigit() for c in tok):
            return tok

    return None


class JiraOtpService:
    """Manages extraction and in-memory caching of Atlassian login verification codes."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.latest_otp: Optional[dict] = None
        self.history: list[dict] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[imaplib.IMAP4_SSL] = None
        self._last_inbox_count: int = -1
        self.last_error: Optional[str] = None
        self.last_status: str = "Uninitialized"
        self.last_check_at: Optional[float] = None

    def start(self) -> None:
        pw = (
            getattr(self.cfg, "jira_evaluator_imap_password", "")
            or ""
        ).strip().replace(" ", "")
        user = (
            getattr(self.cfg, "jira_evaluator_imap_user", "")
            or getattr(self.cfg, "jira_evaluator_email", "")
            or ""
        ).strip()

        if not pw or not user:
            msg = f"Jira evaluator IMAP credentials not configured: user={user!r}, password_provided={bool(pw)}. Set MEESEEK_JIRA_EVALUATOR_IMAP_PASSWORD."
            self.last_status = msg
            log.warning(msg)
            return

        self._stop_event.clear()
        self.last_status = f"Starting IMAP poller for {user}..."
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

    def _reset_client(self) -> None:
        client = self._client
        self._client = None
        self._last_inbox_count = -1
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._reset_client()
        self.last_status = "Stopped"
        log.info("Jira evaluator IMAP poller stopped")

    def set_otp(
        self,
        code: str,
        timestamp: Optional[float] = None,
        subject: str = "Atlassian Verification Code",
        from_addr: str = "noreply@id.atlassian.com",
    ) -> dict:
        code_clean = "".join(c for c in str(code) if c.isalnum()).upper()[:6]
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
        self.last_status = f"Active. Latest OTP: {code_clean}"
        log.info("Jira OTP recorded: %s (at %s)", code_clean, rec_formatted)
        return item

    def get_state(self) -> dict:
        now = time.time()
        with self.lock:
            if self.latest_otp and (now - self.latest_otp["received_at"]) > OTP_TTL_SECONDS:
                self.latest_otp = None
                self.last_status = "Active. Waiting for new login OTP..."
            latest = dict(self.latest_otp) if self.latest_otp else None
            hist = [dict(h) for h in self.history]

        if latest:
            latest["age_seconds"] = max(0, int(now - latest["received_at"]))

        user = (
            getattr(self.cfg, "jira_evaluator_imap_user", "")
            or getattr(self.cfg, "jira_evaluator_email", "")
            or ""
        )
        pw = (getattr(self.cfg, "jira_evaluator_imap_password", "") or "").strip()

        return {
            "evaluator_email": getattr(self.cfg, "jira_evaluator_email", "") or "",
            "evaluator_password": getattr(self.cfg, "jira_evaluator_password", "") or "",
            "jira_url": getattr(self.cfg, "jira_base_url", "") or "",
            "project": getattr(self.cfg, "jira_project", "") or "FSA",
            "latest_otp": latest,
            "history": hist,
            "imap_active": bool(self._thread and self._thread.is_alive()),
            "imap_user": user,
            "imap_password_configured": bool(pw),
            "last_error": self.last_error,
            "last_status": self.last_status,
            "last_check_at": self.last_check_at,
        }

    def _poll_loop(self) -> None:
        interval = max(1.5, min(3.0, float(getattr(self.cfg, "jira_evaluator_imap_interval_s", 2))))
        while not self._stop_event.is_set():
            try:
                self._check_inbox()
                self._stop_event.wait(interval)
            except Exception as e:
                self._reset_client()
                self.last_error = str(e)
                self.last_status = f"IMAP Error: {e}"
                log.error("Error in IMAP inbox check: %s", e)
                # Wait 10 seconds before retry on error to avoid hammering/rate-limiting
                self._stop_event.wait(10)

    def _check_inbox(self) -> None:
        user = (
            getattr(self.cfg, "jira_evaluator_imap_user", "")
            or getattr(self.cfg, "jira_evaluator_email", "")
            or ""
        ).strip().strip("'\"")
        pw = (
            getattr(self.cfg, "jira_evaluator_imap_password", "")
            or ""
        ).strip().strip("'\"").replace(" ", "")
        server = getattr(self.cfg, "jira_evaluator_imap_server", "imap.gmail.com")

        if not user or not pw:
            self.last_status = "Standby: MEESEEK_JIRA_EVALUATOR_IMAP_PASSWORD not configured"
            return

        if self._client is None:
            client = imaplib.IMAP4_SSL(server, 993, timeout=15)
            client.login(user, pw)
            self._client = client
            self._last_inbox_count = -1

        client = self._client
        status, select_data = client.select("INBOX", readonly=True)
        if status != "OK":
            self._reset_client()
            raise RuntimeError(f"IMAP SELECT INBOX returned {status}")

        total = int(select_data[0]) if select_data and select_data[0] and select_data[0].isdigit() else 0
        self.last_check_at = time.time()
        self.last_error = None

        if total == 0:
            self._last_inbox_count = 0
            self.last_status = f"Connected to {server}, INBOX empty"
            return

        # Fast-path (~40ms): if inbox message count hasn't changed since last check
        # and we already scanned up to `total`, skip header/body fetches completely!
        if total == self._last_inbox_count:
            return

        self._last_inbox_count = total
        start = max(1, total - 4)
        msg_ids = [str(i).encode() for i in range(start, total + 1)]

        # Inspect newest 5 messages, newest first
        for mid in reversed(msg_ids):
            # Fetch HEADERS ONLY first — ultra fast, avoids downloading big attachments
            status, header_data = client.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
            if status != "OK" or not header_data:
                continue

            raw_header = header_data[0][1]
            if not isinstance(raw_header, (bytes, bytearray)):
                continue

            msg = email.message_from_bytes(raw_header)
            subject = _decode_header(msg.get("Subject", ""))
            from_hdr = _decode_header(msg.get("From", ""))
            date_hdr = msg.get("Date", "")

            subj_lower = subject.lower()
            from_lower = from_hdr.lower()
            # Skip ordinary Jira issue/comment notification emails (e.g. "[JIRA] (FSA-15) ...")
            if "[jira]" in subj_lower:
                continue

            is_candidate = (
                "id.atlassian" in from_lower
                or "amail.atlassian" in from_lower
                or "verification" in subj_lower
                or "verification" in from_lower
                or "verify" in subj_lower
                or "security code" in subj_lower
                or "code requested" in subj_lower
                or "login" in subj_lower
                or "log in" in subj_lower
                or "sign in" in subj_lower
                or ("atlassian" in from_lower and "code" in subj_lower)
            )
            if not is_candidate:
                continue

            rec_time = time.time()
            try:
                parsed_date = email.utils.parsedate_to_datetime(date_hdr)
                rec_time = parsed_date.timestamp()
            except Exception:
                pass

            # Ignore old verification emails (> 5 minutes old)
            if (time.time() - rec_time) > OTP_TTL_SECONDS:
                continue

            # Check if OTP code is right in the subject!
            code = _extract_otp_code(subject, "")

            # If not in subject, fetch full MIME message for this single verification email
            # and decode quoted-printable / base64 parts cleanly via _extract_body().
            if not code:
                status, full_data = client.fetch(mid, "(BODY.PEEK[])")
                raw_body = ""
                if status == "OK" and full_data and len(full_data) > 0 and len(full_data[0]) > 1:
                    part = full_data[0][1]
                    if isinstance(part, (bytes, bytearray)):
                        try:
                            full_msg = email.message_from_bytes(part)
                            raw_body = _extract_body(full_msg)
                        except Exception:
                            raw_body = part.decode("utf-8", errors="replace")
                code = _extract_otp_code(subject, raw_body)

            if code:
                with self.lock:
                    if self.latest_otp and self.latest_otp.get("code") == code:
                        break

                self.set_otp(
                    code=code,
                    timestamp=rec_time,
                    subject=subject[:100],
                    from_addr=from_hdr[:100],
                )
                log.info("IMAP: Extracted Jira verification code [%s] from email: %s", code, subject)
                break
        else:
            if not self.latest_otp:
                self.last_status = f"Connected to {server} ({user}) - Waiting for login code"
