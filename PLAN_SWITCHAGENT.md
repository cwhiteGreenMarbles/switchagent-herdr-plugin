# switchagent-herdr-plugin — hand the current agent's context to a different agent

## Context

When a coding agent becomes unusable mid-task — Claude usage limit reached, a model
outage, or simply the wrong tool for the next step — the work in that session is
stranded. Restarting elsewhere means re-explaining the goal, the decisions already
made and the state of the tree, by hand.

Herdr (`/opt/homebrew/bin/herdr`) already owns the agent lifecycle on this machine:
it starts agents in panes, tracks their on-disk session identity, and can send them
a prompt. What is missing is the bridge: take the session running in *this* pane,
write its transcript somewhere the next agent can read, start that agent in a
sibling pane, and tell it to read the file.

`switchagent` is that bridge, packaged as a Herdr plugin.

Repo: `/Users/chris/git/switchagent-herdr-plugin` (empty, `main` has no commits,
remote `git@github.com:cwhiteGreenMarbles/switchagent-herdr-plugin.git`).

### Decisions already taken (from the user)

| Question | Answer |
| --- | --- |
| Handoff payload | Raw transcript, rendered as **Markdown** — the new agent summarises it itself |
| Size handling | **Recent turns only**, in a single file. No full transcript is written |
| Loss compensation | A **Context** section built deterministically from the whole session: every user turn, condensed, plus every file the session touched |
| Source selection | **Current session only** — detect the calling pane's agent, no session list |
| Delivery | Write the file to a **temporary directory**, then **start the target agent** and prompt it with the absolute path |
| Form | A **Herdr plugin**, so any agent kind Herdr supports can be the source or the target |

---

## What a Herdr plugin is (verified, not assumed)

Confirmed against the installed binary and against the sibling repo
`/Users/chris/git/pastchats-herdr-plugin`, which is the direct template.

- A plugin is `herdr-plugin.toml` plus executables that Herdr spawns. There is no
  `plugin.json`, no `package.json`, no build step. Python 3 standard library only.
- Manifest sections: `[[panes]]` (`id`, `title`, `command`, `placement`,
  `width`, `height`), `[[actions]]` (`id`, `title`, `command`, `contexts`),
  plus optional `[[events]]`, `[[link_handlers]]`, `[[startup]]`, `[[build]]`.
- `placement` ∈ `overlay | popup | split | tab | zoomed`.
  `contexts` ∈ `global | workspace | tab | pane | selection`.
- Install is `herdr plugin link <dir>` locally, or
  `herdr plugin install OWNER/REPO` from GitHub. There is no central marketplace.
- A keybinding can only invoke an **action**, not a pane, so the action is a
  three-line shim that calls `herdr plugin pane open`.
- Env given to an action: `HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`,
  `HERDR_PLUGIN_ACTION_ID`, `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`,
  `HERDR_PLUGIN_STATE_DIR`, `HERDR_BIN_PATH`, `HERDR_SOCKET_PATH`.
  A pane entrypoint also gets `HERDR_PLUGIN_CONTEXT_JSON`.
  `herdr plugin pane open --env KEY=VALUE` passes extra values through.
- Agent kinds Herdr can start (`herdr agent start --kind`):
  `pi, claude, codex, gemini, cursor, devin, agy, cline, omp, mastracode,
  opencode, copilot, kimi, kiro, droid, amp, grok, hermes, kilo, qodercli,
  qwen, maki`.

Two constraints that shape the code, both learned from `pastchats`:

1. `herdr agent start` blocks up to 30 s waiting for readiness, and a `popup` pane
   is session-modal — so the start must be a detached `Popen` writing to a log in
   `$HERDR_PLUGIN_STATE_DIR`, never a blocking `subprocess.run`.
2. Agent names must match `^[a-z][a-z0-9_-]{0,31}$`.

---

## Design

### Flow

1. Action `switch` fires from a keybinding. It records `HERDR_PANE_ID` (the pane
   Claude is running in) and opens the plugin pane, passing it through as
   `--env SWITCHAGENT_SRC_PANE=<id>`.
2. The pane resolves the **source session**: `herdr agent list` → the entry whose
   `pane_id` matches; that gives `agent` (kind) and `agent_session.value` (the
   on-disk session id) and `cwd`. Fallback if the caller is not an agent pane:
   the most recently modified session whose `cwd` matches the workspace cwd.
3. It shows a short curses list of **target kinds**, ordered: configured default
   first, then installed kinds, then the rest greyed out. Enter selects.
4. It **builds** the handoff document — Context section plus recent turns — and
   writes it to a temp file as the audit copy.
5. It **splits** a sibling pane in the same tab and same cwd, starts the chosen
   agent there detached, waits for it to reach idle, and **injects** the document
   as that agent's first prompt.
6. The pane prints a running log of those steps and stays open until dismissed,
   so a failure is visible rather than silent.

### Output file — one file, recent turns only

Written to a temporary directory, **not** into the repo:

```
$TMPDIR/switchagent-handoff/handoff-<YYYYMMDD-HHMMSS>-<srckind>.md
```

`tempfile.gettempdir()` supplies the base (on macOS that is the per-user private
`$TMPDIR`, mode 0700), the subdirectory is created with mode 0700, and the file
itself is written 0600. A stable named directory is used rather than
`mkdtemp()` so the path is predictable and inspectable while debugging; the
timestamped filename keeps successive switches from colliding. The directory is
overridable with `output_dir` in plugin config. On each run the plugin deletes
its own handoff files older than `retain_days` (default 7).

Keeping these out of the working tree is the point: the file is verbatim session
text and can contain secrets, so it should not be commit-able by accident and
should not survive a reboot.

The complete transcript is **not** written to disk at all.

Dropping the earlier turns loses real information, so the file opens with a
Context section that the plugin assembles deterministically — it has no LLM of
its own, so nothing here is summarised, only selected. Three parts:

**1. Header.** Source kind, session id, cwd, timestamp, total turns in the
session, and how many were dropped to produce the slice.

**2. Context — every user turn, condensed.** One pass over the whole JSONL
collecting genuine user messages in order (the `NOISE_PREFIXES` filter from
`pastchats/sessions/claude.py` removes `<command-name>`,
`<local-command-stdout>` and the `Caveat:` banner). Assistant prose and tool
output are excluded, so this stays small even for a very long session. This is
the intent spine: the original ask plus every correction and change of direction.
Each entry is prefixed with its turn number so the new agent can see where in the
session it sat, and long turns are hard-truncated at a per-entry character budget
with an explicit `…[truncated]` marker.

**3. Context — files touched.** Paths harvested from `tool_use` blocks across the
entire transcript, most-recently-touched first, each tagged with the operations
seen against it. Source-specific extraction, since the record shapes differ:
Claude `message.content[] {type: tool_use, name, input.file_path|input.path}`;
Codex `response_item {type: function_call, name, arguments}`; Cursor is
best-effort and may yield nothing, which the header states.

```markdown
## Files touched
- src/api/handler.ts — Edit ×3, Read
- package.json — Read
```

**4. Recent turns.** The last N (default 60, configurable), agent-neutral
Markdown, one shape regardless of source:

```markdown
## User
...text...

## Assistant
...text...

- tool: Edit
- tool: Bash
```

Explicitly out of scope, having been offered and not chosen: git branch/HEAD/diff
state, and inlining CLAUDE.md or AGENTS.md. The new agent picks those up from the
working directory itself.

### Injection — the handoff goes into the prompt, not behind a file path

The handoff document is **injected into the new agent's context** as the body of
its first prompt, via `herdr agent prompt <name> "<document>" --wait`. It is not
merely referenced. Two reasons: it works for target agents that cannot or will not
read a path outside their working directory, and it guarantees the context is in
the window rather than depending on the agent choosing to open a file.

The temp file remains, but its role changes: it is the audit copy and the
oversize fallback, not the transport.

```
You are taking over an in-progress session from <srckind>. Everything below is
the prior context. Summarise back to me what the task is and what state it is
in, and do not change any files until I confirm.

<the handoff document>
```

The "do not change any files until I confirm" line is deliberate: a fresh agent
acting on a half-understood transcript is the main risk in this flow.

**Size budget.** `herdr agent prompt` sends text through the pane's bracketed
paste, so an unbounded paste is the obvious failure mode — slow, and liable to be
truncated by the terminal or the target agent's own input limit. The document is
therefore assembled against a character budget (`max_inject_chars`, default
40000):

1. The Context section — user turns and files touched — is never trimmed. It is
   the part that cannot be recovered.
2. Recent turns are dropped from the **oldest** end until the document fits.
3. If it still does not fit, per-turn truncation tightens until it does.
4. Whenever anything was dropped for budget, the injected text ends with a line
   naming the absolute temp path holding the untrimmed document, so the agent can
   read the rest if it needs to.

The turn cap (`recent_turns`) and the character budget are both enforced; whichever
binds first wins.

---

## Files

Structure mirrors `pastchats-herdr-plugin` so the two read the same way.

```
herdr-plugin.toml          manifest: [[panes]] switch (popup), [[actions]] switch
open_switch.py             action shim -> herdr plugin pane open --env SWITCHAGENT_SRC_PANE=...
switch.py                  pane entrypoint: resolve source, pick kind, build, launch, inject
switchagent/
  __init__.py
  jsonl.py                 COPY VERBATIM from pastchats-herdr-plugin/sessions/jsonl.py
  model.py                 COPY from pastchats-herdr-plugin/sessions/model.py
  herdr_api.py             COPY from pastchats-herdr-plugin/sessions/herdr_api.py, extended
  sources/__init__.py      SOURCES tuple + per-source dispatch (from sessions/__init__.py)
  sources/claude.py        COPY from pastchats-herdr-plugin/sessions/claude.py
  sources/codex.py         COPY from pastchats-herdr-plugin/sessions/codex.py
  sources/cursor.py        COPY from pastchats-herdr-plugin/sessions/cursor.py
  config.py                read config.toml from HERDR_PLUGIN_CONFIG_DIR
  handoff.py               build the document: header, user-turn spine, files
                           touched, recent turns, budget trimming; write temp copy
  launch.py                split pane, start agent detached, wait for idle, inject
  ui.py                    curses kind picker + scrolling status log
README.md
.gitignore                 __pycache__/, *.pyc
PLAN_SWITCHAGENT.md        this plan, committed alongside the code
```

### Reuse — what is copied rather than written

`pastchats-herdr-plugin` already solved the hard parts; these come across
essentially unchanged and are the reason this is a small job:

- `sessions/jsonl.py` — `iter_records`, `head_records(65536)`,
  `tail_records(131072)` with the torn-first-line drop. Bounded reads matter here
  too: a source transcript can be tens of MB.
- `sessions/claude.py` — `~/.claude/projects/<slug>/<uuid>.jsonl`,
  `load_transcript()` yielding `("user"|"assistant"|"tool", text)` tuples, and the
  `NOISE_PREFIXES` filter that strips `<command-name>`, `<local-command-stdout>`
  and the `Caveat:` banner. That filter is exactly what keeps a handoff readable.
- `sessions/codex.py` — `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`,
  `session_meta` / `event_msg` / `response_item` shapes.
- `sessions/cursor.py` — `~/.cursor/chats/<hash>/<uuid>/store.db`, hex-encoded
  `meta` blob, read-only `file:...?mode=ro` open. Marked `partial_preview`.
- `sessions/herdr_api.py` — the `json.loads(stdout)["result"]` CLI wrapper,
  `live_agents()` keyed on `agent_session.value`, `unique_agent_name()` with the
  `NAME_PATTERN` check, and `start_agent_detached()`.

New code is confined to `handoff.py`, `launch.py`, `config.py`, `switch.py` and the
kind-picker half of `ui.py`.

One extension is needed on the copied source modules: `pastchats` only ever needed
tool *names*, so its `_tool_names()` discards the tool input. `handoff.py` needs
the file paths out of `tool_use.input`, so each source module gains a
`tool_calls()` that yields `(name, path_or_None)`. `_tool_names()` stays as a thin
wrapper over it, so the copied call sites keep working.

### `herdr_api.py` additions

```python
def split_sibling(pane_id, cwd):     # herdr pane layout -> direction; pane split
def agent_prompt(name, text):        # herdr agent prompt <name> <text> --wait
def agent_wait(name, timeout_ms):    # herdr agent wait <name>
def focus_pane(pane_id):             # herdr pane focus
```

Direction rule from `herdr --skill`: split a wide pane `right`, a narrow or tall
pane `down`, decided from `herdr pane layout --pane <id>`.

### Config — `$(herdr plugin config-dir chris.switchagent)/config.toml`

```toml
default_kind = "cursor"      # preselected in the picker
output_dir = ""              # empty = $TMPDIR/switchagent-handoff; absolute path to override
recent_turns = 60            # turns kept in the recent section
max_inject_chars = 40000     # hard ceiling on the injected prompt
max_turn_chars = 4000        # per-turn truncation before the ceiling bites
retain_days = 7              # age at which old temp handoffs are deleted
focus_new_agent = true
```

Read with `tomllib` (Python 3.11+; fall back to defaults if the module or file is
absent, matching pastchats' stdlib-only rule).

---

## Verification

Manual, on this machine — there is no test harness in a Herdr plugin.

1. **Link and inspect**
   ```bash
   herdr plugin link /Users/chris/git/switchagent-herdr-plugin
   herdr plugin list
   herdr plugin action list          # expect chris.switchagent / "switch"
   ```
2. **Build the document in isolation, before touching agent launch.** Run the pane
   entrypoint with `SWITCHAGENT_DRY_RUN=1` from an ordinary shell: it resolves the
   source, writes the temp file, prints the exact text it *would* inject and its
   character count, and starts nothing. Confirm: the user-turn spine holds every
   real user message from this session including the mid-turn corrections; the
   files-touched list names files actually edited; noise records
   (`<command-name>`, `<local-command-stdout>`, the `Caveat:` banner) are absent;
   the temp file is mode 0600 under `$TMPDIR`; and nothing was written into the
   repo.
   Then force the budget with `max_inject_chars = 2000` and confirm recent turns
   drop from the oldest end, the Context section survives intact, and the trailing
   line naming the temp path appears.
3. **Source resolution from a live pane.** From this Claude pane:
   ```bash
   herdr agent list | python3 -m json.tool | grep -A3 pane_id
   ```
   Check the entry for `$HERDR_PANE_ID` carries an `agent_session.value`, and that
   `switchagent/sources/claude.py::find()` resolves it to a real `.jsonl`.
4. **End to end.** Bind a key and invoke it:
   ```toml
   [[keys.command]]
   key = "prefix+w"
   type = "plugin_action"
   command = "chris.switchagent.switch"
   ```
   `herdr server reload-config`, press the key, choose `cursor`. Expect: a sibling
   pane appears with `cursor-agent` running, the handoff arrives as its first
   prompt, and its first reply summarises *this* task accurately with no file
   edits. Accuracy of that summary is the real acceptance test — if the new agent
   misreads the task, the Context section is not carrying enough.
5. **Failure paths.** Confirm each surfaces in the pane rather than hanging:
   invoke from a plain shell pane (no agent → fallback by cwd); choose a kind whose
   binary is not installed; choose a source session with zero usable turns; and
   inject into an agent sitting at an approval dialog, which `herdr agent prompt`
   rejects with `agent_blocked` — that must be reported, not swallowed.
6. **Logs.** `herdr plugin log` and `$HERDR_PLUGIN_STATE_DIR/switch.log` for the
   detached `agent start`.
7. **Cross-source spot check.** Repeat once with a Codex session and once with a
   Cursor session as the *source*, to prove the plugin is not Claude-only.

---

## Notes and limits

- Cursor as a *source* is lossy — `pastchats` flags it `partial_preview` because
  its sqlite blob store has no reliable ordering column. The handoff header will
  say so rather than pretend the transcript is complete.
- The Context section is *selected*, never summarised. The plugin has no model of
  its own, so it cannot compress assistant reasoning — it can only carry the user
  turns and the file list verbatim and let the receiving agent do the compressing.
  Long assistant-side reasoning that was never restated by the user is genuinely
  lost once it falls outside the recent window. That is the accepted cost of the
  recent-only choice.
- Injection depends on the target agent accepting a large bracketed paste. The
  40000-character default is a starting guess, not a measured limit; step 4 of
  verification is where it gets tuned per kind.
- Herdr's kind list has no Gemini CLI session store to read; `gemini` works as a
  *target* only.
- Nothing here helps if Herdr is not running — the plugin is a Herdr artifact by
  construction. A standalone CLI fallback was offered and not chosen; it can be
  added later by making `switch.py` runnable outside a pane with `--kind` and
  `--session`, which the module layout already allows.
