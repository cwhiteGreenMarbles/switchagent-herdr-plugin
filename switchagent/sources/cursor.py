"""Cursor sessions: ~/.cursor/chats/<hash>/<chat-uuid>/.

Each chat directory holds meta.json (cwd, timestamps), prompt_history.json
(the user prompts, in order) and store.db (SQLite).

store.db mixes plain-JSON messages with a binary encoding in its `blobs` table
and has no ordering column, so a Cursor transcript cannot be rebuilt in full.
Previews show the ordered user prompts plus whatever assistant text decodes,
and every Cursor session is flagged `partial_preview`.
"""

import glob
import json
import os
import shutil
import sqlite3

from ..model import Session, clean_title

KIND = "cursor"
RESUME_ARGS = ("--resume",)  # cursor-agent --resume <chat-id>

CHATS = os.path.expanduser("~/.cursor/chats")


def available():
    return shutil.which("cursor-agent") is not None


def list_sessions():
    if not available():
        return []
    sessions = []
    for meta_path in glob.glob(os.path.join(CHATS, "*", "*", "meta.json")):
        session = _read_summary(os.path.dirname(meta_path))
        if session is not None:
            sessions.append(session)
    return sessions


def _read_summary(chat_dir):
    meta = _read_json(os.path.join(chat_dir, "meta.json")) or {}
    cwd = meta.get("cwd") or ""
    if not cwd or not meta.get("hasConversation", True):
        return None

    store = os.path.join(chat_dir, "store.db")
    if not os.path.exists(store):
        return None

    session_id = os.path.basename(chat_dir)
    store_meta = _store_meta(store)
    title = clean_title(store_meta.get("name") or "")
    if not title:
        prompts = _prompts(chat_dir)
        title = clean_title(prompts[0]) if prompts else ""
    if not title:
        return None

    mtime = os.path.getmtime(store)
    if meta.get("updatedAtMs"):
        mtime = max(mtime, meta["updatedAtMs"] / 1000.0)

    return Session(
        kind=KIND,
        session_id=store_meta.get("agentId") or session_id,
        cwd=cwd,
        title=title,
        mtime=mtime,
        path=chat_dir,
        partial_preview=True,
        extra={"model": store_meta.get("lastUsedModel") or ""},
    )


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _prompts(chat_dir):
    data = _read_json(os.path.join(chat_dir, "prompt_history.json"))
    if isinstance(data, list):
        return [p for p in data if isinstance(p, str) and p.strip()]
    return []


def _store_meta(store):
    """The `meta` table holds one hex-encoded JSON value."""
    for value in _query(store, "select value from meta"):
        raw = value[0]
        if not isinstance(raw, str):
            continue
        try:
            decoded = bytes.fromhex(raw).decode("utf-8", errors="replace")
        except ValueError:
            decoded = raw
        try:
            parsed = json.loads(decoded)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _query(store, sql):
    # Read-only: never let the picker write to Cursor's own store.
    uri = "file:%s?mode=ro" % store.replace("?", "%3f").replace("#", "%23")
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        return connection.execute(sql).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def scan(session):
    """([(role, text)], []).

    Cursor keeps no tool-call record this reader can trust, so the file
    activity list is always empty and the handoff header says so.
    """
    return load_transcript(session), []


def load_transcript(session):
    turns = [("user", prompt) for prompt in _prompts(session.path)]

    replies = _assistant_blobs(os.path.join(session.path, "store.db"))
    if replies:
        turns.append(
            (
                "note",
                "Cursor stores replies without an order key, so the %d assistant "
                "messages below are not in conversation order." % len(replies),
            )
        )
        turns.extend(("assistant", text) for text in replies)
    return turns


def _assistant_blobs(store):
    """Assistant text from the blobs that happen to be plain JSON."""
    texts = []
    for row in _query(store, "select data from blobs"):
        data = row[0]
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if not isinstance(data, str):
            continue
        data = data.strip()
        if not data.startswith("{"):
            continue
        try:
            parsed = json.loads(data)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("role") != "assistant":
            continue
        content = parsed.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def find(session_id):
    for chat_dir in glob.glob(os.path.join(CHATS, "*", session_id)):
        session = _read_summary(chat_dir)
        if session is not None:
            return session
    return None
