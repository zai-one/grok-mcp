"""Pure policy/bounds/argv construction for grok_delegate (no I/O).

Unit-testable. Never emits --always-approve. Fail-closed on reserved lanes
and over-cap max_turns.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, MutableMapping, Sequence

# Server-side hard cap for --max-turns (B5).
HARD_CAP_MAX_TURNS = 60

# Lane names that must never be used as delegation targets (B1).
RESERVED_LANE_NAMES = frozenset({"dev", "master", "main"})

# Slug after optional "grok/" prefix.
_LANE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ALWAYS_APPROVE_FLAG = "--always-approve"

# Default binary name; path resolution is runner concern.
DEFAULT_GROK_BIN = "grok"

# Deny rules applied to every execute profile (B2).
_DENY_PUSH = "Bash(git push*)"
_DENY_MERGE = "Bash(git merge*)"
_DENY_CWD_ESCAPE_WRITE = "Write(//**)"
_DENY_CWD_ESCAPE_EDIT = "Edit(//**)"
_DENY_CWD_ESCAPE_READ_ABS = "Read(//**)"
_DENY_HOME_GROK = "Read(~/.grok/**)"
_DENY_HOME_GROK_WRITE = "Write(~/.grok/**)"
_DENY_SECRETS = "Read(**/*secret*)"
_DENY_AUTH = "Read(**/auth.json)"
_DENY_ENV_KEYS = "Bash(*XAI*)"
_DENY_LIVE = "Bash(*live*device*)"
_DENY_PROD = "Bash(*prod*)"
_DENY_ROOT = "Bash(*root*)"
_DENY_DESTRUCTIVE = "Bash(rm -rf*)"
_DENY_FORCE_PUSH = "Bash(git push*--force*)"

_BASE_DENY: tuple[str, ...] = (
    _DENY_PUSH,
    _DENY_MERGE,
    _DENY_FORCE_PUSH,
    _DENY_CWD_ESCAPE_WRITE,
    _DENY_CWD_ESCAPE_EDIT,
    _DENY_HOME_GROK,
    _DENY_HOME_GROK_WRITE,
    _DENY_SECRETS,
    _DENY_AUTH,
    _DENY_ENV_KEYS,
    _DENY_LIVE,
    _DENY_PROD,
    _DENY_ROOT,
    _DENY_DESTRUCTIVE,
)

_BASE_DISALLOWED_TOOLS: tuple[str, ...] = (
    "git_push",
    "git_merge",
    "mcp",
)

# Plan-only: no write/edit/shell mutation.
_PLAN_DENY_WRITE = "Write(**)"
_PLAN_DENY_EDIT = "Edit(**)"
_PLAN_DENY_BASH = "Bash(*)"
_PLAN_DISALLOWED = (
    "Write",
    "Edit",
    "Bash",
    "Shell",
    "git_push",
    "git_merge",
    "mcp",
)

_EXECUTE_ALLOW: tuple[str, ...] = (
    "Read(**)",
    "Write(**)",
    "Edit(**)",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git log*)",
    "Bash(git add*)",
    "Bash(git commit*)",
    "Bash(python*)",
    "Bash(pytest*)",
    "Bash(npm*)",
)

_PLAN_ALLOW: tuple[str, ...] = (
    "Read(**)",
    "Glob(**)",
    "Grep(**)",
)


class GuardError(Exception):
    """Structured fail-closed error from pure guard logic."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"ok": "false", "error": self.code, "message": self.message}


def normalize_lane(name: str | None) -> str:
    """Normalize lane to ``grok/<slug>``; reject reserved / empty / invalid.

    Accepts ``slug``, ``grok/slug``, or ``GROK/slug`` (case-folded prefix only).
    """
    if name is None:
        raise GuardError("LANE_EMPTY", "lane is required")
    raw = str(name).strip()
    if not raw:
        raise GuardError("LANE_EMPTY", "lane is required")

    slug = raw
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) != 2 or parts[0].lower() != "grok" or not parts[1]:
            raise GuardError(
                "LANE_INVALID",
                f"lane must be 'grok/<slug>' or '<slug>', got {raw!r}",
            )
        slug = parts[1]

    lowered = slug.lower()
    if lowered in RESERVED_LANE_NAMES:
        raise GuardError(
            "LANE_RESERVED",
            f"lane {slug!r} is reserved; use a grok/* work lane",
        )
    # Also reject if full name without prefix is reserved (already covered)
    # or if someone passes "dev" as slug.

    if not _LANE_SLUG_RE.match(slug):
        raise GuardError(
            "LANE_INVALID",
            "lane slug must match ^[a-z0-9][a-z0-9-]*$ "
            f"(got {slug!r})",
        )

    return f"grok/{slug}"


def enforce_bounds(max_turns: int | None, *, hard_cap: int = HARD_CAP_MAX_TURNS) -> int:
    """Clamp/validate max_turns against server hard cap. Fail-closed if over cap.

    ``None`` or missing → hard_cap. ``<= 0`` → error. ``> hard_cap`` → error
    (not silent clamp of attacker-supplied over-cap values).
    """
    if hard_cap < 1:
        raise GuardError("HARD_CAP_INVALID", "hard_cap must be >= 1")

    if max_turns is None:
        return hard_cap

    try:
        n = int(max_turns)
    except (TypeError, ValueError) as exc:
        raise GuardError("MAX_TURNS_INVALID", "max_turns must be an integer") from exc

    if n < 1:
        raise GuardError("MAX_TURNS_INVALID", "max_turns must be >= 1")
    if n > hard_cap:
        raise GuardError(
            "MAX_TURNS_CAP",
            f"max_turns {n} exceeds hard cap {hard_cap}",
        )
    return n


def build_permission_profile(plan_only: bool = False) -> dict[str, list[str]]:
    """Build guarded allow/deny/disallowed_tools profile (B2).

    Execute: read/write/edit inside cwd + safe shell; deny push/merge/cwd-escape
    absolute paths, live/device/prod/root, secrets, ~/.grok.
    Plan-only: read-only (deny all write/edit/shell mutation).
    """
    if plan_only:
        deny = list(_BASE_DENY) + [_PLAN_DENY_WRITE, _PLAN_DENY_EDIT, _PLAN_DENY_BASH]
        return {
            "allow": list(_PLAN_ALLOW),
            "deny": deny,
            "disallowed_tools": list(_PLAN_DISALLOWED),
        }

    return {
        "allow": list(_EXECUTE_ALLOW),
        "deny": list(_BASE_DENY) + [_DENY_CWD_ESCAPE_READ_ABS],
        "disallowed_tools": list(_BASE_DISALLOWED_TOOLS),
    }


def build_grok_argv(
    goal: str,
    worktree: str,
    profile: Mapping[str, Sequence[str]],
    max_turns: int,
    *,
    model: str | None = None,
    plan_only: bool = False,
    grok_bin: str = DEFAULT_GROK_BIN,
    hard_cap: int = HARD_CAP_MAX_TURNS,
) -> list[str]:
    """Assemble argv for local grok headless executor.

    Never includes ``--always-approve``. Always pins ``--cwd`` to worktree,
    uses JSON output, enforces max_turns hard cap, and applies profile
    --allow/--deny/--disallowed-tools. ``--no-plan`` only when not plan_only.
    """
    if not goal or not str(goal).strip():
        raise GuardError("GOAL_EMPTY", "goal is required")
    if not worktree or not str(worktree).strip():
        raise GuardError("WORKTREE_EMPTY", "worktree path is required")

    turns = enforce_bounds(max_turns, hard_cap=hard_cap)
    allow = list(profile.get("allow") or ())
    deny = list(profile.get("deny") or ())
    disallowed = list(profile.get("disallowed_tools") or ())

    argv: list[str] = [
        grok_bin,
        "--cwd",
        str(worktree),
        "--output-format",
        "json",
        "--max-turns",
        str(turns),
    ]

    if model:
        argv.extend(["--model", str(model)])

    for rule in allow:
        argv.extend(["--allow", str(rule)])
    for rule in deny:
        argv.extend(["--deny", str(rule)])
    if disallowed:
        # Comma-separated tool names per grok CLI convention.
        argv.extend(["--disallowed-tools", ",".join(str(t) for t in disallowed)])

    if not plan_only:
        argv.append("--no-plan")

    # Goal prompt last as positional (or after flags). Never smuggle always-approve.
    argv.append(str(goal).strip())

    _assert_no_always_approve(argv)
    return argv


def _assert_no_always_approve(argv: Sequence[str]) -> None:
    for part in argv:
        if part == ALWAYS_APPROVE_FLAG or str(part).startswith(f"{ALWAYS_APPROVE_FLAG}="):
            raise GuardError(
                "ALWAYS_APPROVE_FORBIDDEN",
                "argv must never include --always-approve",
            )


def profile_denies_push(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile deny/disallowed covers git push."""
    blob = " ".join(profile.get("deny") or ()).lower()
    tools = " ".join(profile.get("disallowed_tools") or ()).lower()
    return "push" in blob or "git_push" in tools


def profile_denies_merge(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile deny/disallowed covers git merge."""
    blob = " ".join(profile.get("deny") or ()).lower()
    tools = " ".join(profile.get("disallowed_tools") or ()).lower()
    return "merge" in blob or "git_merge" in tools


def profile_denies_cwd_escape(profile: Mapping[str, Sequence[str]]) -> bool:
    """True if profile denies absolute-path / home escape patterns."""
    deny = list(profile.get("deny") or ())
    joined = " ".join(deny)
    return (
        "//**" in joined
        or "~/.grok" in joined
        or any("Write(//" in d or "Edit(//" in d for d in deny)
    )


def assert_argv_safe(argv: Sequence[str]) -> None:
    """Public re-check used by runner before spawn (defense in depth)."""
    _assert_no_always_approve(argv)
    if "--cwd" not in argv:
        raise GuardError("ARGV_MISSING_CWD", "argv must pin --cwd to worktree")
    if "--output-format" not in argv or "json" not in argv:
        raise GuardError("ARGV_MISSING_JSON", "argv must use --output-format json")


def structured_error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured fail-closed error dict for MCP/tool responses."""
    out: MutableMapping[str, Any] = {
        "ok": False,
        "error": code,
        "message": message,
    }
    out.update(extra)
    return dict(out)
