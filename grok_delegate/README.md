# grok-delegate (dev-only local MCP)

Local **stdio MCP** wrapper that delegates a coding goal to the host's `grok` /
`grok.exe` **headless** executor inside an **isolated git worktree** on a
`grok/*` branch, then returns **branch + diffstat** for the integrator's normal
`lane_preflight` → review → merge flow.

This is a **developer tooling** channel (Claude session → local Grok executor).
It is **not** a product surface and **not** the tenant admin MCP bridge.

## Policy note (non-negotiable)

| This package | Product admin-bridge (`tools/mcp/`) |
|---|---|
| Claude → local `grok.exe` (dev machine) | Grok → tenant-scoped PCP HTTP API |
| Worktree isolation on `grok/*` | API key principal, dual-gate writes |
| No push / no merge / no `--always-approve` | No unrestricted platform admin |
| **Does not** import `src/**` | Product control plane |
| **Does not** expand `tools/mcp/` trust | Separate allowlisted tool surface |

Do **not** wire this server into production cabinet, tenant HTTP, or the admin
bridge process. Trust boundaries from `AGENTS.md` / `tools/mcp/README.md` are
unchanged by this package.

## Tools

| MCP tool | Purpose |
|---|---|
| `grok_delegate` | Prepare `../pcp-lanes/<slug>` on `grok/<slug>` from `base_ref` (default `origin/dev`), run headless grok with a guarded permission profile, return `{lane, branch, worktree_path, turns_used, status, summary, changed_files, diffstat}`. |
| `grok_delegate_plan` | Same entry with `plan_only=true` — **read-only** profile (deny write/edit/shell mutation). |

### Input (`grok_delegate`)

```json
{
  "goal": "Implement X with tests…",
  "lane": "my-slice",
  "base_ref": "origin/dev",
  "max_turns": 30,
  "model": "optional-model-id",
  "plan_only": false
}
```

### Output (success)

```json
{
  "ok": true,
  "lane": "grok/my-slice",
  "branch": "grok/my-slice",
  "worktree_path": "…/pcp-lanes/my-slice",
  "turns_used": 12,
  "status": "ok",
  "summary": "…",
  "changed_files": ["path/a.ts"],
  "diffstat": "…"
}
```

Fail-closed errors return `{ "ok": false, "error": "<CODE>", "message": "…" }`
(e.g. `LANE_RESERVED`, `BASE_DIRTY`, `WORKTREE_INSIDE_REPO`, `GROK_MISSING`,
`MAX_TURNS_CAP`).

## Hard bounds

1. **Worktree isolation** — target must resolve **outside** the main repo tree
   (`../pcp-lanes/<slug>` by default). Reserved lanes `dev` / `master` / `main`
   are rejected.
2. **Never `--always-approve`** — only guarded `--allow` / `--deny` /
   `--disallowed-tools`.
3. **Deny push & merge** — profile + runner refuse `git push` / `git merge`
   assembly. No auto-merge, no push to `dev`/`master`.
4. **Deny cwd escape** — absolute-path write/edit and `~/.grok` access denied.
5. **Deny live/device/prod/root**, secret files, destructive `rm -rf`.
6. **Bounded** — server-side `max_turns` hard cap (`HARD_CAP_MAX_TURNS`, default 60)
   + wall-clock timeout + output size cap.
7. **Audit without secrets** — stderr JSON with principal/tool/lane/base_ref/cwd/
   turns/outcome only; goal is length+hash, never raw text; no `~/.grok` contents,
   no full diffs. The process never reads `~/.grok/auth.json` (Grok uses its own
   session; this package does not open that file).
8. **Not product** — no imports from `src/**`; separate from `tools/mcp/`.

## Guarded permission profile

Built by `guard.build_permission_profile(plan_only)`:

**Execute (`plan_only=false`)**

- Allow: in-cwd Read/Write/Edit; limited safe shell (`git status/diff/log/add/commit`,
  `python`, `pytest`, `npm`).
- Deny: `git push*`, `git merge*`, absolute `//**` write/edit, `~/.grok/**`,
  secret/auth paths, live/device/prod/root patterns, `rm -rf*`.
- Disallowed tools: `git_push`, `git_merge`, `mcp`.

**Plan (`plan_only=true`)**

- Allow: Read/Glob/Grep only.
- Deny: all Write/Edit/Bash plus the base deny list.
- Disallowed tools: Write, Edit, Bash, Shell, git_push, git_merge, mcp.

## Layout

```text
tools/grok-delegate/
  guard.py      # pure policy / argv / bounds (no I/O)
  runner.py     # injectable git + subprocess; prepare / run / diffstat
  audit.py      # redaction-safe stderr JSON audit
  server.py     # thin stdio JSON-RPC MCP adapter
  test_grok_delegate.py
  README.md
```

Core logic is transport-independent: tests call `guard` / `runner` / `server.handle_tool_call`
without opening a stdio listen loop.

## Wire into Claude Desktop / local MCP host

Example `claude_desktop_config.json` fragment (paths are host-local):

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": [
        "C:/Users/codex/Documents/Projects/Phone Control Plane/tools/grok-delegate/server.py"
      ],
      "env": {
        "GROK_DELEGATE_REPO_ROOT": "C:/Users/codex/Documents/Projects/Phone Control Plane",
        "GROK_DELEGATE_BIN": "C:/Users/codex/.grok/bin/grok.exe"
      }
    }
  }
}
```

Optional env:

| Env | Meaning |
|---|---|
| `GROK_DELEGATE_REPO_ROOT` | Absolute path to the main git repo |
| `GROK_DELEGATE_BIN` | Path to `grok` / `grok.exe` |

## Run tests

```bash
python -m pytest tools/grok-delegate/test_grok_delegate.py -q
# or
python -m unittest tools.grok-delegate.test_grok_delegate
```

Subprocess and git are **fully mocked**. Tests never spawn real `grok` and never
mutate the host git state.

## Integrator flow (after a successful tool call)

1. Review `branch` + `diffstat` + `changed_files` from the tool result.
2. Open the worktree path; run focused tests / `lane_preflight`.
3. Merge only after review — **this MCP never merges or pushes**.
