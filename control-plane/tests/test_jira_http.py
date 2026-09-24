"""HttpJiraClient wire-format test — verifies the real Jira Cloud REST v3 calls
without a live Jira (monkeypatch urlopen). This is the actual client used when
HOLODECK_JIRA_FAKE is off."""

from __future__ import annotations

import json

from holodeck.config import Config
from holodeck.console import jira as jira_mod


class _Resp:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client_capturing(monkeypatch, payload=b"{}"):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["data"] = req.data
        return _Resp(payload)

    monkeypatch.setattr(jira_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = Config(jira_base_url="https://acme.atlassian.net", jira_email="e@acme", jira_token="tok")
    return jira_mod.HttpJiraClient(cfg), captured


def test_post_comment_builds_v3_request(monkeypatch):
    client, captured = _client_capturing(monkeypatch)
    client.post_comment("CPL-1", "hello world")
    assert captured["url"] == "https://acme.atlassian.net/rest/api/3/issue/CPL-1/comment"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"].startswith("Basic ")  # email:token basic auth
    body = json.loads(captured["data"])
    assert body["body"]["type"] == "doc"  # ADF
    assert body["body"]["content"][0]["content"][0]["text"] == "hello world"


def test_post_comment_renders_markdown_as_adf(monkeypatch):
    client, captured = _client_capturing(monkeypatch)
    # No blank line between the heading and the bullets — matches real agent
    # narration (see the COMP-4953 transcript this regression is based on).
    md = (
        "## Verification done\n"
        "- `pytest tests/x.py` — 10 passed\n"
        "- see [the PR](https://github.com/org/repo/pull/1)\n\n"
        "**Nothing** needed from you."
    )
    client.post_comment("CPL-1", md)
    blocks = json.loads(captured["data"])["body"]["content"]

    heading = blocks[0]
    assert heading["type"] == "heading" and heading["attrs"]["level"] == 2
    assert heading["content"][0]["text"] == "Verification done"

    bullets = blocks[1]
    assert bullets["type"] == "bulletList"
    code_run = bullets["content"][0]["content"][0]["content"][0]
    assert code_run["text"] == "pytest tests/x.py" and code_run["marks"][0]["type"] == "code"
    link_run = bullets["content"][1]["content"][0]["content"][1]  # [0] is "see " plain text
    assert link_run["text"] == "the PR"
    assert link_run["marks"][0] == {"type": "link", "attrs": {"href": "https://github.com/org/repo/pull/1"}}

    para = blocks[2]
    assert para["type"] == "paragraph"
    bold_run = para["content"][0]
    assert bold_run["text"] == "Nothing" and bold_run["marks"][0]["type"] == "strong"


def test_get_issue_reads_summary_and_description(monkeypatch):
    payload = json.dumps({"fields": {"summary": "S", "description": "the spec"}}).encode()
    client, captured = _client_capturing(monkeypatch, payload=payload)
    got = client.get_issue("CPL-1")
    assert "/rest/api/3/issue/CPL-1?fields=summary,description" in captured["url"]
    assert captured["method"] == "GET"
    assert got == {"summary": "S", "description": "the spec", "labels": [], "issuetype": ""}


def test_set_labels_builds_v3_update_request(monkeypatch):
    client, captured = _client_capturing(monkeypatch)
    client.set_labels("CPL-1", add=["holodeck:halt"], remove=["holodeck"])
    assert captured["url"] == "https://acme.atlassian.net/rest/api/3/issue/CPL-1"
    assert captured["method"] == "PUT"
    body = json.loads(captured["data"])
    assert body == {"update": {"labels": [{"add": "holodeck:halt"}, {"remove": "holodeck"}]}}


def test_set_labels_noop_with_no_labels_makes_no_request(monkeypatch):
    client, captured = _client_capturing(monkeypatch)
    client.set_labels("CPL-1")
    assert captured == {}  # no HTTP call at all


def test_missing_base_url_raises():
    import pytest
    cfg = Config(jira_base_url="", jira_email="e", jira_token="t")
    with pytest.raises(jira_mod.JiraError):
        jira_mod.HttpJiraClient(cfg).post_comment("CPL-1", "x")
