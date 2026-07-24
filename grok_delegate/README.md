# grok-delegate (dev-only local MCP)

Local **stdio MCP** wrapper that delegates a coding goal to the host's `grok` /
`grok.exe` **headless** executor (`--single` / `-p`) inside an **isolated git
worktree** on a `grok/*` branch, then returns **branch + diffstat** for the
integrator's normal review → merge flow.

This is a **developer tooling** channel. It is **not** a product surface and
**not** a tenant admin MCP bridge.

## Policy note (non-negotiable)

| This package | Product admin-bridge |
|---|---|
| Claude → local `grok.exe` (dev machine) | Grok → tenant-scoped product HTTP API |
| Worktree isolation on `grok/*` | API key principal, dual-gate writes |
| No push / no merge / no `--always-approve` | No unrestricted platform admin |
| **Does not** import product `src/**` | Product control plane |

Do **not** wire this server into production cabinet, tenant HTTP, or an admin
bridge process until R1–R5 acceptance and an explicit integrator step.

## Tools

| MCP tool | Purpose |
|---|---|
| `grok_delegate` | Prepare external worktree on `grok/<slug>` from `base_ref` (default `origin/dev`), run headless grok with a guarded permission profile, return `{lane, branch, worktree_path, turns_used, status, summary, changed_files, diffstat}`. |
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

`grok_bin` is **not** a client argument (set `GROK_DELEGATE_BIN` if needed).
Client `repo_root` / `lanes_parent` are validated against server/env pins — not
blindly trusted.

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

Fail-closed errors return `{ "ok": false, "error": "<CODE>", "message": "…" }`.

## Hard bounds

1. **Worktree isolation** — target must resolve **outside** the main repo tree.
   Reserved lanes `dev` / `master` / `main` are rejected.
2. **Never `--always-approve`** — only guarded `--allow` / `--deny` /
   `--disallowed-tools` plus `--permission-mode` (`dontAsk` or `plan`; never
   `bypassPermissions`).
3. **Headless only** — argv uses `--single` (documented headless interface), not
   a bare interactive positional prompt.
4. **Deny push & merge** — profile + runner refuse `git push` / `git merge`
   assembly. No auto-merge, no push.
5. **Shell is git-only** — no `python*` / `pytest*` / `npm*` allows (those would
   nullify shell denies). Full test toolchains stay with the integrator when needed.
6. **Path denies are best-effort** — UNC + Windows drive globs + `~/.grok`;
   relative `../` escape is a **non-guarantee** (see `EVIDENCE.md`).
7. **Bounded** — server-side `max_turns` hard cap + wall-clock timeout + output
   size cap.
8. **Audit without secrets** — stderr JSON with principal/tool/lane/base_ref/cwd/
   turns/outcome; goal is length+hash only; process never reads `~/.grok/auth.json`.

## Guarded permission profile

Built by `guard.build_permission_profile(plan_only)`:

**Execute (`plan_only=false`)**

- Allow: in-cwd Read/Write/Edit; git shell only (`status/diff/log/add/commit`).
- Deny: `git push*`, `git merge*`, UNC + Windows absolute write/edit/read globs,
  `~/.grok/**`, secret/auth paths, live/device/prod/root patterns, `rm -rf*`.
- Disallowed tools: `git_push`, `git_merge`, `mcp`.
- Permission mode: `dontAsk`.

**Plan (`plan_only=true`)**

- Allow: Read/Glob/Grep only.
- Deny: all Write/Edit/Bash plus the base deny list.
- Permission mode: `plan`.

## Layout

```text
grok_delegate/
  guard.py      # pure policy / argv / bounds (no I/O)
  runner.py     # injectable git + subprocess; prepare / run / diffstat
  audit.py      # redaction-safe stderr JSON audit
  server.py     # thin stdio JSON-RPC MCP adapter
  README.md
tests/
  test_grok_delegate.py
EVIDENCE.md
```

## Env

| Env | Meaning |
|---|---|
| `GROK_DELEGATE_REPO_ROOT` | Absolute path to the main git repo (authoritative) |
| `GROK_DELEGATE_BIN` | Path or name of `grok` / `grok.exe` only |
| `GROK_DELEGATE_LANES_PARENT` | Optional pin for worktree parent directory |

## Run tests

```bash
py -3 -m pytest tests -q
```

Subprocess and git are **fully mocked**. Tests never spawn real `grok` and never
mutate the host git state.

## Integrator flow (after a successful tool call)

1. Review `branch` + `diffstat` + `changed_files` from the tool result.
2. Open the worktree path; run focused tests / preflight.
3. Merge only after review — **this MCP never merges or pushes**.
