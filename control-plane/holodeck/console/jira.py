"""The Jira seam — inbound triggers/replies + outbound comments.

Two inbound transports feed the same bridge: a pushed webhook (`parse_webhook`)
and — when we can't register a webhook — polling (`search_labeled` for trigger
tickets + `list_comments` for replies). `JiraClient` also posts comments back and
fetches the ticket spec, with a `FakeJiraClient` for tests/local and an
`HttpJiraClient` for real Jira (Cloud REST v3), config-gated on creds.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


class JiraError(RuntimeError):
    pass


@dataclass
class JiraEvent:
    kind: str  # "comment" | "label"
    issue_key: str
    author: str  # accountId or displayName (for the loop guard)
    body: str = ""  # comment text (kind == "comment")
    label: str = ""  # label added (kind == "label")


# ---- webhook parsing ----------------------------------------------------

def _author(a: Optional[dict]) -> str:
    a = a or {}
    return a.get("accountId") or a.get("displayName") or ""


# Block-level ADF node types — a flattened line boundary goes after each of
# these to prevent sibling nodes from collapsing into a single run-on string
# without line breaks.
_BLOCK_ADF_TYPES = {
    "paragraph", "heading", "blockquote", "bulletList", "orderedList",
    "listItem", "codeBlock", "rule", "table", "tableRow", "panel", "expand",
}


def _flatten_adf(node) -> str:
    """Jira comment/description bodies are plain strings (v2) or ADF docs (v3).
    Best-effort flatten of ADF into text, preserving block boundaries (and
    explicit hardBreak nodes) as newlines."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "text":
            return node.get("text", "")
        if node_type == "hardBreak":
            return "\n"
        text = "".join(_flatten_adf(c) for c in node.get("content", []))
        return text + "\n" if node_type in _BLOCK_ADF_TYPES else text
    if isinstance(node, list):
        return "".join(_flatten_adf(c) for c in node)
    return ""


def _added_labels(from_s: str, to_s: str) -> list[str]:
    return sorted(set((to_s or "").split()) - set((from_s or "").split()))


# ---- markdown -> ADF (best-effort, not a general parser) -----------------
# Agent narration relayed to Jira (bridge.py) is plain markdown — headings,
# **bold**, `code`, [links](url), bullet lists. Jira's ADF has no raw-markdown
# ingestion, so without this a comment prints literal '##'/'**' characters
# instead of rendering. Covers exactly the subset agents actually use; not
# meant to handle arbitrary markdown (nested lists, tables, etc.).
_INLINE_RE = re.compile(
    r"\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\)"
    r"|\*\*(?P<bold>[^*]+)\*\*"
    r"|\*(?P<italic>[^*]+)\*"
    r"|_(?P<italic_u>[^_]+)_"
    r"|`(?P<code>[^`]+)`"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*•]\s+(.*)$")


def _inline_nodes(text: str) -> list[dict]:
    """Parse **bold** / *italic* / `code` / [text](url) within one line into ADF text
    nodes with marks; everything else passes through as plain text."""
    nodes: list[dict] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos:m.start()]})
        if m.group("link_text") is not None:
            nodes.append({"type": "text", "text": m.group("link_text"),
                          "marks": [{"type": "link", "attrs": {"href": m.group("link_url")}}]})
        elif m.group("bold") is not None:
            nodes.append({"type": "text", "text": m.group("bold"),
                          "marks": [{"type": "strong"}]})
        elif m.group("italic") is not None:
            nodes.append({"type": "text", "text": m.group("italic"),
                          "marks": [{"type": "em"}]})
        elif m.group("italic_u") is not None:
            nodes.append({"type": "text", "text": m.group("italic_u"),
                          "marks": [{"type": "em"}]})
        elif m.group("code") is not None:
            nodes.append({"type": "text", "text": m.group("code"),
                          "marks": [{"type": "code"}]})
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes or [{"type": "text", "text": ""}]


def _markdown_to_adf_blocks(body: str) -> list[dict]:
    """Line-by-line -> ADF block nodes. Supports headings, bullet lists (including '•'),
    code blocks (```), and paragraphs."""
    blocks: list[dict] = []
    bullets: list[str] = []
    para: list[str] = []
    in_code = False
    code_lang = "text"
    code_lines: list[str] = []

    def flush_bullets() -> None:
        if bullets:
            items = [{"type": "listItem",
                      "content": [{"type": "paragraph", "content": _inline_nodes(t)}]}
                     for t in bullets]
            blocks.append({"type": "bulletList", "content": items})
            bullets.clear()

    def flush_para() -> None:
        if para:
            content: list[dict] = []
            for i, line in enumerate(para):
                if i:
                    content.append({"type": "hardBreak"})
                content.extend(_inline_nodes(line))
            blocks.append({"type": "paragraph", "content": content})
            para.clear()

    for raw_line in body.strip().split("\n"):
        line = raw_line.strip()
        if in_code:
            if line.startswith("```"):
                in_code = False
                c_text = "\n".join(code_lines)
                blocks.append({
                    "type": "codeBlock",
                    "attrs": {"language": code_lang or "text"},
                    "content": [{"type": "text", "text": c_text or " "}]
                })
                code_lines.clear()
            else:
                code_lines.append(raw_line)
            continue

        if line.startswith("```"):
            flush_bullets()
            flush_para()
            in_code = True
            code_lang = line[3:].strip() or "text"
            code_lines = []
            continue

        if not line:
            flush_bullets()
            flush_para()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_bullets()
            flush_para()
            level = min(len(heading.group(1)), 6)
            blocks.append({"type": "heading", "attrs": {"level": level},
                            "content": _inline_nodes(heading.group(2))})
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_para()
            bullets.append(bullet.group(1))
            continue
        flush_bullets()
        para.append(line)

    flush_bullets()
    flush_para()
    return blocks or [{"type": "paragraph", "content": []}]


def parse_webhook(payload: dict) -> Optional[JiraEvent]:
    """Normalize a Jira Cloud webhook to a JiraEvent, or None if irrelevant."""
    event = payload.get("webhookEvent", "")
    key = (payload.get("issue") or {}).get("key")
    if not key:
        return None
    if event == "comment_created":
        c = payload.get("comment") or {}
        body = _flatten_adf(c.get("body")).strip()
        if body:
            return JiraEvent("comment", key, _author(c.get("author")), body=body)
    elif event == "jira:issue_updated":
        author = _author(payload.get("user"))
        for item in ((payload.get("changelog") or {}).get("items") or []):
            if item.get("field") == "labels":
                added = _added_labels(item.get("fromString", ""), item.get("toString", ""))
                if added:
                    return JiraEvent("label", key, author, label=added[0])
    return None


# ---- client -------------------------------------------------------------

@runtime_checkable
class JiraClient(Protocol):
    def post_comment(self, issue_key: str, body: str, *,
                      links: Optional[list[tuple[str, str]]] = None) -> str:
        """Post a comment; `links` is an optional list of (label, url) pairs
        rendered as a clickable-links line ABOVE `body`, e.g. [("Session", "https://..."),
        ("Preview", "https://...")]. -> new comment id"""
        ...
    def get_issue(self, issue_key: str) -> dict: ...  # {"summary", "description", "labels", "issuetype"}
    def search_labeled(self, project: str, label: str) -> list[str]: ...  # -> issue keys
    def list_comments(self, issue_key: str) -> list[dict]: ...  # [{id, author, body, created}]

    def set_labels(self, issue_key: str, *, add: Optional[list[str]] = None,
                    remove: Optional[list[str]] = None) -> None:
        """Add/remove labels in one call. Best-effort from the caller's side (the
        bridge never lets a labeling failure break the action that triggered it)."""
        ...


class FakeJiraClient:
    """In-memory Jira for tests/local. Records comments (with monotonic ids so the
    reply-watermark logic is exercised), serves canned issues, and lets a test add
    a labelled ticket or a human reply."""

    def __init__(self, issues: Optional[dict] = None, labels: Optional[dict] = None,
                 bot: str = "bot") -> None:
        self.comments: list[tuple[str, str]] = []          # back-compat: what WE posted
        self._issues = issues or {}
        self._labels = {k: set(v) for k, v in (labels or {}).items()}  # issue -> {label}
        self._by_issue: dict[str, list[dict]] = {}         # issue -> [comment dicts]
        self._seq = 0
        self.bot = bot

    def _add(self, issue_key: str, author: str, body: str) -> str:
        self._seq += 1
        cid = str(self._seq)   # Jira comment ids are a monotonic global sequence
        self._by_issue.setdefault(issue_key, []).append(
            {"id": cid, "author": author, "body": body, "created": cid})
        return cid

    # --- what the bridge calls ---
    def post_comment(self, issue_key: str, body: str, *,
                      links: Optional[list[tuple[str, str]]] = None) -> str:
        # Test/local visibility: fold link labels into the recorded body so
        # assertions can check for them without needing real ADF parsing.
        full = body if not links else (
            " · ".join(f"[{label}]({url})" for label, url in links) + "\n\n" + body)
        self.comments.append((issue_key, full))
        return self._add(issue_key, self.bot, full)

    def get_issue(self, issue_key: str) -> dict:
        base = self._issues.get(
            issue_key, {"summary": issue_key, "description": f"(spec for {issue_key})"})
        return {**base, "labels": sorted(self._labels.get(issue_key, set()))}

    def search_labeled(self, project: str, label: str) -> list[str]:
        return [k for k, ls in self._labels.items() if label in ls]

    def list_comments(self, issue_key: str) -> list[dict]:
        return list(self._by_issue.get(issue_key, []))

    def set_labels(self, issue_key: str, *, add: Optional[list[str]] = None,
                    remove: Optional[list[str]] = None) -> None:
        labels = self._labels.setdefault(issue_key, set())
        for l in (remove or []):
            labels.discard(l)
        for l in (add or []):
            labels.add(l)

    # --- test helpers (simulate the human in Jira) ---
    def add_label(self, issue_key: str, label: str) -> None:
        self._labels.setdefault(issue_key, set()).add(label)

    def labels_of(self, issue_key: str) -> set[str]:
        return set(self._labels.get(issue_key, set()))

    def reply(self, issue_key: str, body: str, author: str = "human") -> str:
        return self._add(issue_key, author, body)


class HttpJiraClient:
    """Jira Cloud REST v3. Basic auth = email:api_token. Real but untested
    without creds — enable via HOLODECK_JIRA_* config."""

    def __init__(self, cfg) -> None:
        self.base = (cfg.jira_base_url or "").rstrip("/")
        self._auth = (
            base64.b64encode(f"{cfg.jira_email}:{cfg.jira_token}".encode()).decode()
            if cfg.jira_email else ""
        )

    def _req(self, method: str, path: str, body: Optional[dict] = None):
        if not self.base:
            raise JiraError("HOLODECK_JIRA_BASE_URL not set")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._auth:
            headers["Authorization"] = f"Basic {self._auth}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise JiraError(f"jira {method} {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as e:
            raise JiraError(f"jira unreachable: {e.reason}")

    @staticmethod
    def _links_paragraph(links: list[tuple[str, str]]) -> dict:
        """One ADF paragraph rendering each (label, url) as a real clickable
        link mark — not a raw pasted URL relying on Jira's own autolinker —
        separated by " · ", e.g. "Session · Preview"."""
        content: list[dict] = []
        for i, (label, href) in enumerate(links):
            if i:
                content.append({"type": "text", "text": " · "})
            content.append({"type": "text", "text": label,
                            "marks": [{"type": "link", "attrs": {"href": href}}]})
        return {"type": "paragraph", "content": content}

    def post_comment(self, issue_key: str, body: str, *,
                      links: Optional[list[tuple[str, str]]] = None) -> str:
        content = []
        if links:
            content.append(self._links_paragraph(links))
        if body:
            content.extend(_markdown_to_adf_blocks(body))
        adf = {"type": "doc", "version": 1,
               "content": content or [{"type": "paragraph", "content": []}]}
        d = self._req("POST", f"/rest/api/3/issue/{issue_key}/comment", {"body": adf})
        return str(d.get("id", "")) if isinstance(d, dict) else ""

    def get_issue(self, issue_key: str) -> dict:
        d = self._req("GET", f"/rest/api/3/issue/{issue_key}?fields=summary,description,labels,issuetype")
        f = d.get("fields", {}) if isinstance(d, dict) else {}
        issuetype = f.get("issuetype") or {}
        return {"summary": f.get("summary", ""), "description": _flatten_adf(f.get("description")),
                "labels": f.get("labels") or [], "issuetype": issuetype.get("name") or ""}

    def search_labeled(self, project: str, label: str) -> list[str]:
        """Issue keys in `project` carrying `label`. Newest first so a burst of
        labels is handled in a sensible order. Uses the v3 JQL search endpoint."""
        jql = f'project = "{project}" AND labels = "{label}" ORDER BY created DESC'
        q = urllib.parse.urlencode({"jql": jql, "fields": "key", "maxResults": 50})
        d = self._req("GET", f"/rest/api/3/search/jql?{q}")
        issues = d.get("issues", []) if isinstance(d, dict) else []
        return [i["key"] for i in issues if i.get("key")]

    def list_comments(self, issue_key: str) -> list[dict]:
        """All comments on a ticket, oldest→newest, normalized to
        {id, author (accountId), body (flattened), created}."""
        d = self._req("GET", f"/rest/api/3/issue/{issue_key}/comment?orderBy=created&maxResults=100")
        out = []
        for c in (d.get("comments", []) if isinstance(d, dict) else []):
            out.append({
                "id": str(c.get("id", "")),
                "author": _author(c.get("author")),
                "body": _flatten_adf(c.get("body")).strip(),
                "created": c.get("created", ""),
            })
        return out

    def set_labels(self, issue_key: str, *, add: Optional[list[str]] = None,
                    remove: Optional[list[str]] = None) -> None:
        """One `update` op per label add/remove — the standard v3 partial-update
        shape (avoids a read-modify-write race on the full labels list)."""
        ops = [{"add": l} for l in (add or [])] + [{"remove": l} for l in (remove or [])]
        if not ops:
            return
        self._req("PUT", f"/rest/api/3/issue/{issue_key}", {"update": {"labels": ops}})


def build_jira_client(cfg):
    return FakeJiraClient() if cfg.jira_fake else HttpJiraClient(cfg)
