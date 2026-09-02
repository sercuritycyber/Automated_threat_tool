# agent-bus — Claude ↔ Antigravity handoff

Two agents share this VM. **Claude Code** owns the codebase and does the coding.
**Antigravity (Gemini)** is the extra pair of hands for work that is *not*
coding or that plays to Gemini's strengths: research, doc drafting, data
wrangling, image / diagram generation, long-context summarisation, web
trawling, format conversion, spec writing.

## Directories

    to-antigravity/todo/    Claude drops a task here → Antigravity picks it up
    to-antigravity/doing/   Antigravity moves it here while working
    to-antigravity/done/    finished, with a RESULT section appended

    to-claude/todo|doing|done/     the same, the other direction

    shared/     scratch files, artefacts, datasets both agents read + write
    archive/    old tasks moved out of done/ once acknowledged

## Task file format  (Markdown, name: `<UTC-ISO>-<slug>.md`)

    ---
    id: 20260903T001500Z-favicon-set
    from: claude
    to: antigravity
    kind: asset            # research | doc | data | asset | convert | review | other
    status: todo           # todo → doing → done
    priority: normal       # low | normal | high
    created: 2026-09-03T00:15:00Z
    ---

    ## Task
    <what is needed, in enough detail to act without a reply>

    ## Inputs
    <paths under shared/, URLs, constraints>

    ## Deliverable
    <exact file(s) to produce, and where to put them — usually under shared/>

    ## Acceptance
    <how the requester will check it is done>

    --- Antigravity appends below on completion ---

    ## RESULT
    status: done | blocked
    files: shared/...
    notes: <what was done; anything the requester should know>

## Rules

- Claude does **not** do a non-coding task itself when it can be delegated
  here. Antigravity does **not** touch application source (`worker/`, `bin/`,
  `lib/`, `web/`, `prompts/`) — it may only write under `shared/` and its own
  `done/` files. Anything code goes back to Claude via `to-claude/todo/`.
- One task per file. Never edit a file sitting in the other agent's `todo/`.
- If blocked, set `status: blocked` in RESULT and say exactly what's needed.
- **Secrets never go in a task file.** Reference `config/env` keys by name only.
- Keep `shared/` tidy: prefix files with the task id they belong to.

## Poll loop (either agent)

    for f in ~/agent-bus/to-<me>/todo/*.md; do
      [ -e "$f" ] || continue
      mv "$f" ~/agent-bus/to-<me>/doing/
      # ... work it, append ## RESULT ...
      mv ~/agent-bus/to-<me>/doing/"$(basename "$f")" ~/agent-bus/to-<me>/done/
    done
