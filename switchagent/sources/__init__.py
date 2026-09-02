"""Readers for coding-agent session stores.

Every source exposes the same four names, so the rest of the plugin never has
to know which agent a session came from:

    KIND            the herdr agent kind, e.g. "claude"
    list_sessions() every session in the store
    find(id)        one session by its own session id
    scan(session)   ([(role, text)], [(tool_name, path)]) in one pass
"""

from . import claude, codex, cursor

SOURCES = (claude, cursor, codex)

BY_KIND = {source.KIND: source for source in SOURCES}


def scan(session):
    """Turns and file activity for a session, whichever agent wrote it."""
    source = BY_KIND.get(session.kind)
    if source is None:
        return [], []
    return source.scan(session)


def find(kind, session_id):
    """One session by kind and id, or None."""
    source = BY_KIND.get(kind)
    if source is None:
        return None
    try:
        return source.find(session_id)
    except Exception:
        # A broken store must not take the switch down with it.
        return None


def list_sessions():
    found = []
    for source in SOURCES:
        try:
            found.extend(source.list_sessions())
        except Exception:
            continue
    return found
