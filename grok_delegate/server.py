#!/usr/bin/env python3
"""Dev-only stdio MCP entry for grok_delegate (thin transport over guard/runner).

Core logic lives in guard.py / runner.py and is transport-independent.
This module is a minimal JSON-RPC 2.0 MCP-ish stdio adapter (no product
admin-bridge, no src/** imports, no trust expansion of tools/mcp/).

Tools:
  - grok_delegate       — prepare worktree + headless run + diffstat
  - grok_delegate_plan  — same with plan_only=true (read-only profile)
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping, TextIO

# Allow package and flat execution.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from .audit import (  # type: ignore[no-redef]
        build_delegation_audit,
        emit as audit_emit,
    )
    from .guard import GuardError, structured_error, validate_grok_bin  # type: ignore[no-redef]
    from .runner import delegate, is_path_inside  # type: ignore[no-redef]
except ImportError:  # flat import when package dir is on sys.path
    from audit import (  # noqa: E402
        build_delegation_audit,
        emit as audit_emit,
    )
    from guard import GuardError, structured_error, validate_grok_bin  # noqa: E402
    from runner import delegate, is_path_inside  # noqa: E402

SERVER_NAME = "grok-delegate"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL_DELEGATE = "grok_delegate"
TOOL_PLAN = "grok_delegate_plan"

_TOOL_DESCRIPTIONS = {
    TOOL_DELEGATE: (
        "Delegate a coding goal to local headless grok in an isolated git worktree "
        "on a grok/* branch. Returns branch + diffstat. No push, no merge, no "
        "--always-approve. Dev-only — not the product admin-bridge."
    ),
    TOOL_PLAN: (
        "Read-only plan variant of grok_delegate (plan_only=true). Does not allow "
        "write/edit/shell mutation in the permission profile."
    ),
}

# R5: grok_bin is intentionally absent from the client schema.
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "description": "Coding goal for the executor"},
        "lane": {
            "type": "string",
            "description": "Lane slug or grok/<slug> (not dev/master/main)",
        },
        "base_ref": {
            "type": "string",
            "description": "Git base ref (default origin/dev)",
            "default": "origin/dev",
        },
        "max_turns": {
            "type": "integer",
            "description": "Max agent turns (server hard-capped)",
        },
        "model": {"type": "string", "description": "Optional model id"},
        "plan_only": {
            "type": "boolean",
            "description": "If true, use read-only permission profile",
            "default": False,
        },
        "repo_root": {
            "type": "string",
            "description": (
                "Optional absolute path to main repo root; must match server "
                "configured GROK_DELEGATE_REPO_ROOT when that env is set"
            ),
        },
        "lanes_parent": {
            "type": "string",
            "description": (
                "Optional parent dir for worktrees; rejected if inside repo_root "
                "or outside GROK_DELEGATE_LANES_PARENT when that env is set"
            ),
        },
    },
    "required": ["goal", "lane"],
    "additionalProperties": False,
}


def default_repo_root() -> Path:
    env = os.environ.get("GROK_DELEGATE_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # Package lives at <project>/grok_delegate/server.py
    return Path(__file__).resolve().parents[1]


def resolve_trusted_repo_root(
    args: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Path:
    """Resolve repo_root without blindly trusting the client (R5).

    Server/env root is authoritative. Client ``repo_root`` is accepted only when
    it resolves to the same path as the configured root.
    """
    configured = (repo_root or default_repo_root()).resolve()
    client = args.get("repo_root")
    if client is None or str(client).strip() == "":
        return configured
    try:
        client_path = Path(str(client)).resolve()
    except OSError as exc:
        raise GuardError("REPO_ROOT_INVALID", f"invalid repo_root: {exc}") from exc
    if client_path != configured:
        # When env pin is set, never accept a different client root.
        if os.environ.get("GROK_DELEGATE_REPO_ROOT"):
            raise GuardError(
                "REPO_ROOT_UNTRUSTED",
                "client repo_root does not match GROK_DELEGATE_REPO_ROOT",
            )
        # Without env pin: still reject path that is not an absolute existing dir
        # equal to the injected server repo_root (tests pass repo_root=...).
        if repo_root is not None and client_path != Path(repo_root).resolve():
            raise GuardError(
                "REPO_ROOT_UNTRUSTED",
                "client repo_root does not match server-configured repo_root",
            )
        if repo_root is None:
            # No server pin and no env: refuse arbitrary client roots (fail closed).
            raise GuardError(
                "REPO_ROOT_UNTRUSTED",
                "client repo_root requires GROK_DELEGATE_REPO_ROOT or server repo_root pin",
            )
    return configured


def resolve_trusted_lanes_parent(
    args: Mapping[str, Any],
    *,
    repo_root: Path,
) -> Path | None:
    """Resolve lanes_parent with confinement checks (R5).

    - If GROK_DELEGATE_LANES_PARENT is set, client value must resolve inside it
      (or equal it); if client omits lanes_parent, use the env path.
    - Target parent must never resolve inside repo_root (enforced again in
      prepare_worktree for the final worktree path).
    """
    env_parent = os.environ.get("GROK_DELEGATE_LANES_PARENT")
    client = args.get("lanes_parent")

    if env_parent:
        pinned = Path(env_parent).resolve()
        if client is None or str(client).strip() == "":
            candidate = pinned
        else:
            candidate = Path(str(client)).resolve()
            if candidate != pinned and not is_path_inside(candidate, pinned):
                raise GuardError(
                    "LANES_PARENT_UNTRUSTED",
                    "client lanes_parent is outside GROK_DELEGATE_LANES_PARENT",
                )
    elif client is None or str(client).strip() == "":
        return None
    else:
        candidate = Path(str(client)).resolve()

    if is_path_inside(candidate, repo_root):
        raise GuardError(
            "LANES_PARENT_INSIDE_REPO",
            "lanes_parent must not resolve inside the main repo working tree",
        )
    return candidate


def resolve_server_grok_bin(args: Mapping[str, Any]) -> str:
    """Grok binary from env/config only — never from undeclared client args (R5)."""
    if "grok_bin" in args and args.get("grok_bin") is not None:
        # Fail closed even if additionalProperties were bypassed.
        validate_grok_bin(str(args.get("grok_bin")), from_client=True)
    env_bin = os.environ.get("GROK_DELEGATE_BIN")
    return validate_grok_bin(env_bin, from_client=False)


def handle_tool_call(
    name: str,
    arguments: Mapping[str, Any] | None,
    *,
    repo_root: Path | None = None,
    git_runner=None,
    subprocess_runner=None,
    which=None,
    audit_stream: TextIO | None = None,
    principal: str = "local-dev",
) -> dict[str, Any]:
    """Transport-independent tool handler (callable without stdio)."""
    args = dict(arguments or {})
    plan_only = bool(args.get("plan_only", False)) or name == TOOL_PLAN

    if name not in (TOOL_DELEGATE, TOOL_PLAN):
        err = structured_error("TOOL_UNKNOWN", f"unknown tool: {name}")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    goal = args.get("goal")
    lane = args.get("lane")
    if not goal or not str(goal).strip():
        err = structured_error("GOAL_EMPTY", "goal is required")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err
    if not lane or not str(lane).strip():
        err = structured_error("LANE_EMPTY", "lane is required")
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    try:
        root = resolve_trusted_repo_root(args, repo_root=repo_root)
        lanes_parent = resolve_trusted_lanes_parent(args, repo_root=root)
        grok_bin = resolve_server_grok_bin(args)
    except GuardError as exc:
        err = structured_error(exc.code, exc.message)
        _audit_failure(
            principal=principal,
            tool=name,
            args=args,
            result=err,
            stream=audit_stream,
        )
        return err

    base_ref = str(args.get("base_ref") or "origin/dev")
    max_turns = args.get("max_turns")
    model = args.get("model")

    try:
        result = delegate(
            goal=str(goal),
            lane=str(lane),
            repo_root=root,
            base_ref=base_ref,
            max_turns=int(max_turns) if max_turns is not None else None,
            model=str(model) if model else None,
            plan_only=plan_only,
            lanes_parent=lanes_parent,
            grok_bin=str(grok_bin),
            git_runner=git_runner,
            subprocess_runner=subprocess_runner,
            which=which,
        )
    except GuardError as exc:
        result = structured_error(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — surface as structured tool error
        result = structured_error(
            "INTERNAL_ERROR",
            f"{type(exc).__name__}: {exc}",
        )

    outcome = "ok" if result.get("ok") else "error"
    try:
        audit_emit(
            build_delegation_audit(
                principal=principal,
                tool=name,
                lane=result.get("lane") or str(lane),
                base_ref=base_ref,
                cwd=result.get("worktree_path"),
                turns_used=result.get("turns_used"),
                outcome=outcome,
                goal=str(goal),
                plan_only=plan_only,
                error=result.get("error"),
                changed_file_count=len(result.get("changed_files") or []),
                branch=result.get("branch"),
                worktree_path=result.get("worktree_path"),
                status=result.get("status"),
            ),
            stream=audit_stream,
        )
    except Exception:
        pass
    return result


def _audit_failure(
    *,
    principal: str,
    tool: str,
    args: Mapping[str, Any],
    result: Mapping[str, Any],
    stream: TextIO | None,
) -> None:
    try:
        audit_emit(
            build_delegation_audit(
                principal=principal,
                tool=tool,
                lane=str(args.get("lane") or ""),
                base_ref=str(args.get("base_ref") or ""),
                cwd=None,
                turns_used=None,
                outcome="error",
                goal=str(args.get("goal") or "") or None,
                plan_only=bool(args.get("plan_only", False)),
                error=result.get("error"),
                status="error",
            ),
            stream=stream,
        )
    except Exception:
        # Audit must not crash the tool path; best-effort only.
        pass


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": TOOL_DELEGATE,
            "description": _TOOL_DESCRIPTIONS[TOOL_DELEGATE],
            "inputSchema": _INPUT_SCHEMA,
        },
        {
            "name": TOOL_PLAN,
            "description": _TOOL_DESCRIPTIONS[TOOL_PLAN],
            "inputSchema": {
                **_INPUT_SCHEMA,
                "properties": {
                    **_INPUT_SCHEMA["properties"],
                    "plan_only": {
                        "type": "boolean",
                        "const": True,
                        "default": True,
                    },
                },
            },
        },
    ]


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def handle_jsonrpc(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; returns response or None for notifications."""
    if message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(message.get("id"), -32600, "invalid jsonrpc version")

    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    is_notification = "id" not in message

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _jsonrpc_result(req_id, result)

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return None if is_notification else _jsonrpc_result(
            req_id, {"tools": list_tools()}
        )

    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else {}
        if not name:
            return _jsonrpc_error(req_id, -32602, "tools/call requires name")
        tool_result = handle_tool_call(str(name), arguments or {})
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_result, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": tool_result,
            "isError": not bool(tool_result.get("ok")),
        }
        return None if is_notification else _jsonrpc_result(req_id, payload)

    if method == "ping":
        return None if is_notification else _jsonrpc_result(req_id, {})

    if is_notification:
        return None
    return _jsonrpc_error(req_id, -32601, f"method not found: {method}")


def serve_stdio(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Blocking stdio JSON-RPC loop (line-delimited or Content-Length framed)."""
    inn = stdin or sys.stdin
    out = stdout or sys.stdout

    while True:
        chunk = inn.readline()
        if chunk == "":
            break
        line = chunk.strip()
        if not line:
            continue

        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            inn.readline()  # blank line after headers
            body = inn.read(length)
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                continue
            response = handle_jsonrpc(message)
            if response is not None:
                raw = json.dumps(response, ensure_ascii=False)
                out.write(f"Content-Length: {len(raw.encode('utf-8'))}\r\n\r\n{raw}")
                out.flush()
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            err = _jsonrpc_error(None, -32700, "parse error")
            out.write(json.dumps(err) + "\n")
            out.flush()
            continue

        try:
            response = handle_jsonrpc(message)
        except Exception as exc:  # noqa: BLE001
            response = _jsonrpc_error(
                message.get("id"),
                -32603,
                f"internal error: {type(exc).__name__}",
            )
            traceback.print_exc(file=sys.stderr)

        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        sys.stdout.write(
            "grok-delegate MCP (dev-only)\n"
            "Usage: python -m grok_delegate.server\n"
            "Env: GROK_DELEGATE_REPO_ROOT, GROK_DELEGATE_BIN, GROK_DELEGATE_LANES_PARENT\n"
            "NOT the product admin-bridge (tools/mcp/).\n"
        )
        return 0
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
