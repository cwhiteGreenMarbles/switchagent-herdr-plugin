"""Claude Code sessions: ~/.claude/projects/<slug>/<session-uuid>.jsonl."""

import glob
import os

from .. import jsonl
from ..model import Session, clean_title

KIND = "claude"
RESUME_ARGS = ("--resume",)  # claude --resume <session-id>

PROJECTS = os.path.expanduser("~/.claude/projects")

# Wrappers Claude Code writes around slash commands and local shell output.
# They are real user records but say nothing about the conversation.
NOISE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "Caveat: The messages below",
)


def list_sessions():
    sessions = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
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
    tail = jsonl.tail_records(path)

    cwd = ""
    session_id = ""
    for record in head + tail:
        cwd = cwd or record.get("cwd") or ""
        session_id = session_id or record.get("sessionId") or ""
        if cwd and session_id:
            break
    if not session_id:
        session_id = os.path.splitext(os.path.basename(path))[0]
    if not cwd:
        return None

    title = _title(head, tail)
    if not title:
        # No real exchange in this file — an empty or aborted session.
        return None

    return Session(
        kind=KIND,
        session_id=session_id,
        cwd=cwd,
        title=title,
        mtime=stat.st_mtime,
        path=path,
        extra={"branch": _branch(head + tail)},
    )


def _title(head, tail):
    # Claude Code names sessions itself; that title beats anything we derive.
    for record in reversed(tail):
        if record.get("type") == "ai-title" and record.get("aiTitle"):
            return clean_title(record["aiTitle"])
    for record in head:
        if record.get("type") == "ai-title" and record.get("aiTitle"):
            return clean_title(record["aiTitle"])
    for record in head:
        text = _user_text(record)
        if text:
            return clean_title(text)
    for record in reversed(tail):
        if record.get("type") == "last-prompt" and record.get("lastPrompt"):
            return clean_title(record["lastPrompt"])
    return ""


def _branch(records):
    for record in records:
        if record.get("gitBranch"):
            return record["gitBranch"]
    return ""


def _user_text(record):
    """Text of a genuine user turn, or "" for anything synthetic."""
    if record.get("type") != "user":
        return ""
    text = _content_text(record.get("message", {}).get("content"))
    if not text:
        return ""
    stripped = text.lstrip()
    for prefix in NOISE_PREFIXES:
        if stripped.startswith(prefix):
            return ""
    return text


def _content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts)


# Not every user message is stored as a user record with plain text. Three
# other shapes carry real user input, and a handoff that drops them loses the
# corrections that steered the session:
#
#   * a message typed while a turn was still running is delivered as an
#     attachment, not as a user turn;
#   * an answer to a question the agent asked comes back inside a tool result;
#   * so does the reason a tool call was rejected.
ANSWERED = "The user answered:"
SAID = "the user said:"
REJECTED = "The user doesn't want to proceed"
ANSWER_TRAILER = "Read the answers care"


def _queued_prompt(record):
    """A message the user typed mid-turn, or "".

    Only human-origin attachments count: background task notifications are
    queued through the same mechanism.
    """
    if record.get("type") != "attachment":
        return ""
    attachment = record.get("attachment") or {}
    if attachment.get("type") != "queued_command":
        return ""
    if (attachment.get("origin") or {}).get("kind") != "human":
        return ""
    prompt = attachment.get("prompt")
    return prompt if isinstance(prompt, str) and prompt.strip() else ""


def _result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    return _content_text(content)


def _user_replies(content):
    """User words quoted back inside tool results: answers and refusals."""
    replies = []
    if not isinstance(content, list):
        return replies
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        text = _result_text(block).lstrip()
        if not text:
            continue
        # Both markers are only trusted in their canonical framing. A tool that
        # merely printed the phrase — grepping a transcript, say — must not be
        # mistaken for the user speaking.
        if text.startswith(ANSWERED):
            answer = text[len(ANSWERED) :].strip()
            cut = answer.find(ANSWER_TRAILER)
            if cut > 0:
                answer = answer[:cut].strip()
            if answer:
                replies.append(answer)
            continue
        if not block.get("is_error") or not text.startswith(REJECTED):
            continue
        marker = text.find(SAID)
        if marker >= 0:
            said = text[marker + len(SAID) :].strip()
            if said:
                replies.append(said)
    return replies


# Where the tools that touch a file keep the path. Read/Edit/Write use
# file_path; the notebook tools and some MCP tools use their own key.
PATH_KEYS = ("file_path", "filePath", "notebook_path", "path")


def _tool_path(payload):
    if not isinstance(payload, dict):
        return None
    for key in PATH_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_calls(content):
    """[(tool_name, path or None)] for the tool_use blocks in one content list."""
    calls = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((block.get("name") or "tool", _tool_path(block.get("input"))))
    return calls


def _tool_names(content):
    return [name for name, _ in tool_calls(content)]


def scan(session):
    """One pass over the transcript: ([(role, text)], [(tool_name, path)]).

    The turns and the file activity come from the same records, and the file is
    large, so they are collected together rather than in two passes.
    """
    turns = []
    calls = []
    for record in jsonl.iter_records(session.path):
        kind = record.get("type")
        if kind == "attachment":
            prompt = _queued_prompt(record)
            if prompt:
                turns.append(("user", prompt))
        elif kind == "user":
            text = _user_text(record)
            if text:
                turns.append(("user", text))
            for reply in _user_replies(record.get("message", {}).get("content")):
                turns.append(("user", reply))
        elif kind == "assistant":
            content = record.get("message", {}).get("content")
            text = _content_text(content)
            if text.strip():
                turns.append(("assistant", text))
            for name, path in tool_calls(content):
                turns.append(("tool", name))
                if path:
                    calls.append((name, path))
    return turns, calls


def load_transcript(session):
    """[(role, text)] in order. Tool calls collapse to one line each."""
    return scan(session)[0]


def find(session_id):
    """A live session that the listing skipped, e.g. one with no messages yet."""
    for path in glob.glob(os.path.join(PROJECTS, "*", "%s.jsonl" % session_id)):
        session = _read_summary(path)
        if session is not None:
            return session
        head = jsonl.head_records(path)
        cwd = next((r.get("cwd") for r in head if r.get("cwd")), "")
        if cwd:
            return Session(
                kind=KIND,
                session_id=session_id,
                cwd=cwd,
                title="(no messages yet)",
                mtime=os.path.getmtime(path),
                path=path,
            )
    return None
