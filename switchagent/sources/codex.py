"""Codex sessions: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.

Codex is not installed on every machine. When the CLI is missing the source
returns nothing, so its rows never appear in a list you could not act on.
"""

import glob
import json
import os
import shutil

from .. import jsonl
from ..model import Session, clean_title

KIND = "codex"
RESUME_ARGS = ("resume",)  # codex resume <session-id> — a subcommand, not a flag

SESSIONS = os.path.expanduser("~/.codex/sessions")


def available():
    return shutil.which("codex") is not None


def list_sessions():
    if not available():
        return []
    sessions = []
    pattern = os.path.join(SESSIONS, "*", "*", "*", "rollout-*.jsonl")
    for path in glob.glob(pattern):
        session = _read_summary(path)
        if session is not None:
            sessions.append(session)
    return sessions


def _read_summary(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat.st_size == 0:
        return None

    head = jsonl.head_records(path)
    meta = {}
    for record in head:
        if record.get("type") == "session_meta":
            meta = record.get("payload") or {}
            break
    cwd = meta.get("cwd") or ""
    session_id = meta.get("id") or ""
    if not cwd or not session_id:
        return None

    title = _title(head) or clean_title(meta.get("timestamp") or "")
    if not title:
        return None

    return Session(
        kind=KIND,
        session_id=session_id,
        cwd=cwd,
        title=title,
        mtime=stat.st_mtime,
        path=path,
        extra={"model": meta.get("model_provider") or ""},
    )


def _title(records):
    for record in records:
        text = _user_text(record)
        if text:
            return clean_title(text)
    return ""


def _user_text(record):
    payload = record.get("payload") or {}
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        return payload.get("message") or ""
    return ""


PATH_KEYS = ("path", "file_path", "filePath")


def _call_path(payload):
    """Codex passes tool arguments as a JSON string, not an object."""
    raw = payload.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    for key in PATH_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def scan(session):
    """One pass: ([(role, text)], [(tool_name, path)])."""
    turns = []
    calls = []
    for record in jsonl.iter_records(session.path):
        payload = record.get("payload") or {}
        kind = record.get("type")
        if kind == "event_msg":
            event = payload.get("type")
            if event == "user_message" and payload.get("message"):
                turns.append(("user", payload["message"]))
            elif event == "agent_message" and payload.get("message"):
                turns.append(("assistant", payload["message"]))
        elif kind == "response_item" and payload.get("type") == "function_call":
            name = payload.get("name") or "tool"
            turns.append(("tool", name))
            path = _call_path(payload)
            if path:
                calls.append((name, path))
    return turns, calls


def load_transcript(session):
    """[(role, text)]. Codex writes the clean pair as event_msg records."""
    return scan(session)[0]


def find(session_id):
    pattern = os.path.join(SESSIONS, "*", "*", "*", "rollout-*-%s.jsonl" % session_id)
    for path in glob.glob(pattern):
        session = _read_summary(path)
        if session is not None:
            return session
    return None
