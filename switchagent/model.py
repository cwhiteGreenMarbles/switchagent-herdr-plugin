"""The one session shape every source produces."""

import os
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    kind: str  # "claude" | "cursor" | "codex" — matches herdr agent kinds
    session_id: str  # passed to the resume flag; joins to agent_session.value
    cwd: str
    title: str
    mtime: float
    path: str  # transcript file or directory, for lazy preview loading
    partial_preview: bool = False  # true when the transcript cannot be read in full

    # filled in from `herdr agent list` when the session is still open
    live_pane_id: str = ""
    live_status: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_live(self):
        return bool(self.live_pane_id)

    @property
    def project(self):
        return os.path.basename(self.cwd.rstrip("/")) or self.cwd

    def age(self):
        """Short elapsed time, e.g. "3m", "2h", "5d"."""
        seconds = max(0, time.time() - self.mtime)
        if seconds < 60:
            return "%ds" % int(seconds)
        if seconds < 3600:
            return "%dm" % int(seconds // 60)
        if seconds < 86400:
            return "%dh" % int(seconds // 3600)
        return "%dd" % int(seconds // 86400)


WEEK_SECONDS = 7 * 24 * 3600


def recent(sessions, max_age=WEEK_SECONDS):
    """Drop sessions older than `max_age` or whose directory is gone.

    Sorted newest first. `max_age` of None keeps every session.
    """
    cutoff = 0 if max_age is None else time.time() - max_age
    alive = [
        s for s in sessions if s.mtime >= cutoff and s.cwd and os.path.isdir(s.cwd)
    ]
    alive.sort(key=lambda s: s.mtime, reverse=True)
    return alive


def clean_title(text, width=90):
    """One tidy line out of arbitrary message text."""
    if not text:
        return ""
    line = " ".join(text.split())
    if len(line) > width:
        line = line[: width - 1].rstrip() + "…"
    return line
