# switchagent

A [Herdr](https://herdr.dev) plugin that hands an in-progress agent session to a
different agent.

Claude hits its usage limit halfway through a task. Rather than re-explaining the
job to Cursor by hand, press a key: switchagent reads the session running in the
current pane, builds a handoff, starts the agent you choose in a sibling pane, and
gives it that handoff as its first prompt.

Sources it can read: **claude**, **codex**, **cursor**.
Targets it can start: the 22 kinds listed in `KINDS` (`switchagent/launch.py`) —
claude, cursor, codex, gemini, copilot, opencode, amp, droid, grok, kimi, kiro,
qwen, devin, agy, cline, omp, mastracode, hermes, kilo, qodercli, pi, maki.

## Install

Herdr 0.8.0 or newer.

```bash
herdr plugin link /path/to/switchagent-herdr-plugin
herdr plugin list
```

Bind it to a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+a"
type = "plugin_action"
command = "chris.switchagent.switch"
description = "switch agent"
```

```bash
herdr server reload-config
```

Python 3 standard library only. No build step, no dependencies. Reading the
config file needs Python 3.11 for `tomllib`; on anything older the file is
ignored and the defaults below still apply.

## Which session gets handed over

The pane's agent entry is asked first, because it carries both the kind and the
id that agent's own store uses. If the calling pane has no agent — a plain shell
pane, say — the newest session of any readable kind in that pane's working
directory is used instead. The picker's subtitle names which of the two happened.

## What gets handed over

Only the recent turns are carried, so the earlier conversation is represented two
other ways. The plugin has no model of its own — it cannot summarise — so it
*selects* rather than compresses:

1. **Header** — source agent, session id, working directory, turn counts.
2. **Context, in the user's own words** — every user message from the whole
   session, in order, replies stripped out. This is the task plus every
   correction to it, and it is never dropped to save space.
3. **Files touched** — paths from the session's tool calls, most recent first,
   with the operations seen against each. Capped at 80 paths, with a count of
   the remainder.
4. **Recent turns** — the last 60, verbatim.

For a Claude source, "the user's own words" includes three shapes that are not
stored as plain user turns and would otherwise be lost: a message typed while a
turn was still running, an answer given to a question the agent asked, and the
reason a tool call was rejected.

The document is injected as the new agent's first prompt, not left in a file for
it to find, so the context is in its window whether or not it goes looking. It is
prefixed with an instruction to summarise the task back and change no files until
confirmed. A copy is written to `$TMPDIR/switchagent-handoff/` (directory `0700`,
files `0600`) as the audit copy and the oversize fallback — never into your repo,
because it is verbatim session text and can contain anything the session saw.

Where a session runs long, the document is trimmed to `max_inject_chars`: recent
turns go first, from the oldest end, because the context section is the part that
cannot be recovered from anywhere else. If the context alone still overruns, the
per-turn cap halves until it reaches 200 characters, and only then is the text
hard-cut. When anything is dropped, the injected text ends with the path to the
untrimmed copy.

## Configuration

`$(herdr plugin config-dir chris.switchagent)/config.toml`, all optional:

```toml
default_kind = "cursor"      # preselected in the picker
output_dir = ""              # empty: $TMPDIR/switchagent-handoff
recent_turns = 60            # turns kept verbatim
max_inject_chars = 40000     # ceiling on the injected prompt
max_turn_chars = 4000        # per-turn truncation
retain_days = 7              # age at which old handoffs are deleted
focus_new_agent = true
```

A malformed config file is ignored rather than fatal. Any key can be overridden
per run with an environment variable, which is how the budget gets exercised:

```bash
SWITCHAGENT_DRY_RUN=1 SWITCHAGENT_MAX_INJECT_CHARS=2000 python3 switch.py
```

`SWITCHAGENT_DRY_RUN=1` resolves the session, writes the file, prints exactly what
would be injected, and starts nothing.

The popup lists every kind, with the ones not on `PATH` marked `(not installed)`
and not selectable. `↑↓` or `j`/`k` moves, `enter` switches, `q` cancels. Once a
target is chosen the same pane becomes a progress log, and it waits for a keypress
before closing so a failure is readable. When `HERDR_PLUGIN_STATE_DIR` is set, the
outcome and any traceback also go to `switch.log` in that directory.

## Known limits

- **Files written through the shell are invisible.** The file list comes from
  tool-call arguments, so `Write` and `Edit` are seen but `cat > file` inside a
  Bash call is not. A session that does most of its editing through the shell
  will show a short list.
- **Assistant reasoning outside the recent window is lost.** If a decision was
  never restated by the user and has scrolled past the recent turns, it is not in
  the handoff. That is the cost of carrying recent turns only.
- **Cursor sources are partial.** Cursor stores its chats without an ordering
  key, so the transcript cannot be rebuilt faithfully: the user prompts come out
  in order, the assistant messages that decode are appended after them out of
  order, and both the handoff header and a note above them say so. Cursor records
  no tool activity this reader trusts, so its file list is always empty. Cursor
  is a first-class *target*.
- **Codex tool paths are best-effort.** Its shell tool records a command, not a
  path, so file activity from a Codex session is usually empty.
- **First-run modals can swallow the handoff.** An agent showing "do you trust
  this directory?" is reported by Herdr as idle and ready, so the first
  injection lands in the dialog instead of the prompt. Cursor is started with
  `--trust` to avoid it, and an injection that visibly went nowhere is retried
  once. An agent with a startup dialog and no way to suppress it will need its
  own entry in `STARTUP_ARGS`.
- **A target has 90 seconds to become ready.** Past that, or if it settles into
  `blocked`, the switch stops with the path to the handoff file so you can send
  it by hand.
- **The 40000-character ceiling is a starting guess**, not a measured limit for
  any particular agent. Lower it if a target truncates the paste.
- Gemini and the other kinds Herdr can start have no session store this plugin
  reads; they work as targets only.

## Layout

```
herdr-plugin.toml   manifest: one popup pane, one action
open_switch.py      action shim; passes the calling pane id to the popup
switch.py           resolve the session, choose a target, build, launch, inject
switchagent/
  sources/          claude, codex and cursor session readers
  handoff.py        the document, the context spine and the size budget
  launch.py         pane split, agent start, injection
  herdr_api.py      wrapper over the herdr CLI
  jsonl.py          bounded reads over large transcripts
  config.py  model.py  ui.py
```

The session readers and the CLI wrapper began as copies from
[pastchats-herdr-plugin](https://github.com/cwhiteGreenMarbles/pastchats-herdr-plugin).
