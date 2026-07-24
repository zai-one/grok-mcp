# grok_delegate — ROUND4 evidence

Working directory: `C:\Users\codex\Documents\Projects\MCP\Grok CLI`  
Date: 2026-07-24  
Builds on Round 3 (`EVIDENCE.md`, R1–R5).

## Summary

| Area | Result |
|---|---|
| Status tools (4 read-only) | **Shipped** |
| Multi-root allowlist | **Shipped** (fail-closed) |
| `--sandbox` profiles | **Discovered + applied by default** (see caveats) |
| Server path normalization | **Shipped** (`confine_path_to_root`) |
| Extra CLI params | **Shipped** with validation |
| `--self-test` / `--smoke-delegate` | **Shipped**; live smoke **PASS** |
| `.mcp.json` registration | **Not done** (owner decision, out of scope) |
| Phone Control Plane commits | **None** |

## New MCP tools

| Tool | CLI surface | Mutating? |
|---|---|---|
| `grok_delegate_status` | aggregate: `version --json`, `models` (auth probe), git `--version`, config | No |
| `grok_delegate_doctor` | `grok doctor --json` only | No (`fix` hard-rejected) |
| `grok_delegate_models` | `grok models` | No |
| `grok_delegate_inspect` | `grok inspect --json` for allowlisted root | No |

Secrets: status never reads `auth.json`; auth = success of read-only `models` probe + login phrase. Inspect JSON redacts auth-looking keys/paths.

## Multi-root allowlist

| Input | Behavior | Code |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | `;` or JSON array of absolute paths | `server.load_allowed_roots` / `guard.parse_allowed_roots_env` |
| `GROK_DELEGATE_REPO_ROOT` | single-entry allowlist fallback | same |
| empty / missing | `ALLOWED_ROOTS_EMPTY` + setup hint | `server.resolve_trusted_repo_root` |
| client root on list | accepted after `Path.resolve()` | `guard.path_in_allowlist` |
| client root off list / `../` escape | `REPO_ROOT_UNTRUSTED` | post-resolve equality only |
| per-root lanes | default sibling `pcp-lanes`; env pin still supported | `resolve_trusted_lanes_parent` |

Tests: `MultiRootAllowlistTests` (accept / reject / `..` / empty).

## Sandbox decision

### Profiles discovered (live host docs + help)

Sources: `~/.grok/docs/user-guide/18-sandbox.md`, `05-configuration.md` (`GROK_SANDBOX`), `grok --help` (`--sandbox <PROFILE>`).

| Profile | Role |
|---|---|
| `off` | no sandbox (default in upstream CLI) |
| `workspace` | read everywhere; write CWD + temp + `~/.grok/` |
| `devbox` | disposable VM profile |
| `read-only` | write only session/temp |
| `strict` | read mostly CWD; tightest built-in |

**Defaults in grok_delegate:**

- Execute → `--sandbox workspace`
- Plan-only → `--sandbox read-only`
- Override: tool arg `sandbox` or env `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` (`off` disables)

**Not invented:** only the documented built-in names are accepted (`KNOWN_SANDBOX_PROFILES`).

### OS enforcement honesty

Docs state kernel primitives are **Landlock (Linux)** and **Seatbelt (macOS)**.  
On **Windows**, the CLI **accepts** `--sandbox` (live probe with `--sandbox workspace -p …` succeeded), but this evidence **does not claim OS-level FS confinement** on Windows. If the kernel backend is missing, upstream may warn and continue without enforcement.

| Vector | Round 3 | Round 4 |
|---|---|---|
| Permission-engine absolute path denies | best-effort | best-effort (unchanged) |
| Relative `../` via agent Write/Edit | **non-guarantee** | **still non-guarantee on Windows**; on Linux/macOS with working sandbox, workspace/strict limits writes to CWD — **not proven on this host as OS-closed** |
| Server-controlled path args (`--cwd`, inspect root) | pin only | **closed** via `guard.confine_path_to_root` / allowlist resolve |
| True OS sandbox | off | **flag on by default**; enforcement **platform-dependent** |

## Worktree decision: CLI `-w/--worktree` vs own `prepare_worktree`

| Option | Pros | Cons |
|---|---|---|
| CLI `--worktree` / `--worktree-ref` | native, less code | less control over external `pcp-lanes` layout; branch naming; harder to reject reserved lanes / inside-repo paths before spawn; couples isolation to grok session lifecycle |
| Own `runner.prepare_worktree` | external lanes parent, reserved-lane reject, dirty-base fail-closed, explicit branch `grok/<slug>`, integrator-owned merge path | extra git orchestration |

**Decision: keep own `prepare_worktree`.**  
CLI `-w/--worktree` is **not** emitted on the execute path. Documented in README; argv tests assert absence of `--worktree` / `-w`.

## Extra CLI parameters (validated)

| Param | Bound / validation | Argv |
|---|---|---|
| `model` | max 128 chars | `--model` |
| `max_turns` | hard cap 60 | `--max-turns` |
| `reasoning_effort` | allowlist | `--reasoning-effort` |
| `rules` | max 8k chars | `--rules` |
| `json_schema` | JSON object, max 16k | `--json-schema` |
| `no_subagents` | bool | `--no-subagents` |
| `disable_web_search` | bool | `--disable-web-search` |
| `resume` / `continue_session` / `fork_session` / `session_id` | UUID rules; fork requires resume/continue | matching flags |
| `sandbox` | known profiles only | `--sandbox` |
| `--tools` allowlist | from profile | `--tools` + still `--disallowed-tools` |

Still **never**: `--always-approve`, `bypassPermissions`, client `grok_bin`, auto-merge/push.

## Self-check (no Claude restart)

```bash
py -3 -m grok_delegate --self-test
py -3 -m grok_delegate --smoke-delegate
```

| Check | Result (this host) |
|---|---|
| `--self-test` | **PASS** (binary, version, auth, git, initialize, tools/list, 4 status tools; no delegate) |
| `--smoke-delegate` | **PASS** — live plan-only headless, `summary: SMOKE_OK`, max_turns=2, temp git repo |

Captured under implementer scratch: `self-test-round4.log`, `smoke-delegate-round4.log`.

## Gates

| Gate | Result |
|---|---|
| `py -3 -m pytest tests -q` | **82 passed** (+ prior subtests) |
| `py -3 -m py_compile` on package modules | **exit 0** |
| B1–B9 / no always-approve / no bypass / no client grok_bin | **retained** in tests |

## Invariants retained (B1–B9)

1. Worktree outside main repo  
2. Never `--always-approve`  
3. Never `bypassPermissions`  
4. No auto-merge / push  
5. Bounded turns + timeout  
6. Audit without secrets  
7. Fail-closed bounds  
8. No client `grok_bin`  
9. Headless `--single` only  

## Non-goals (still)

- Registering in `.mcp.json`  
- Exposing `sessions delete`, `doctor fix`, `logout`, `update`, `setup`, `plugin *`, `mcp *`  
- Product admin-bridge / multi-tenant SaaS  
- Claiming Windows OS sandbox parity with Linux/macOS  
