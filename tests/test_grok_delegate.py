#!/usr/bin/env python3
"""Unit tests for grok_delegate (mocked git + subprocess only).

No real grok spawn, no real git mutation. Drives shipped guard/runner/audit/
server entry points. Adversarial cases assert behavior of argv/profile/runner
paths (R4), not theater-only substring presence.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_PKG = _ROOT / "grok_delegate"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from grok_delegate import audit as audit_mod  # noqa: E402
from grok_delegate import guard  # noqa: E402
from grok_delegate import runner  # noqa: E402
from grok_delegate import server  # noqa: E402


class MockGit:
    """Configurable git mock — never touches the host repository."""

    def __init__(
        self,
        *,
        version_ok: bool = True,
        base_ok: bool = True,
        dirty: bool = False,
        worktree_add_ok: bool = True,
        existing_branch: str | None = None,
        name_only: str = "grok_delegate/guard.py\n",
        porcelain: str = " M grok_delegate/guard.py\n",
        diff_stat: str = " grok_delegate/guard.py | 10 ++++++++++\n 1 file changed\n",
        create_target: bool = True,
    ) -> None:
        self.version_ok = version_ok
        self.base_ok = base_ok
        self.dirty = dirty
        self.worktree_add_ok = worktree_add_ok
        self.existing_branch = existing_branch
        self.name_only = name_only
        self.porcelain = porcelain
        self.diff_stat = diff_stat
        self.create_target = create_target
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: list[str] | tuple[str, ...],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)

        lowered = [a.lower() for a in argv]
        for forbidden in ("push", "merge"):
            if forbidden in lowered:
                return {
                    "args": argv,
                    "returncode": 99,
                    "stdout": "",
                    "stderr": f"mock refused git {forbidden}",
                    "timedOut": False,
                }

        if argv[:1] == ["--version"] or (argv and argv[0] == "--version"):
            if not self.version_ok:
                return {
                    "args": argv,
                    "returncode": 127,
                    "stdout": "",
                    "stderr": "git not found",
                    "timedOut": False,
                    "missing": True,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": "git version 2.45.0\n",
                "stderr": "",
                "timedOut": False,
            }

        if "rev-parse" in argv and "--verify" in argv:
            if not self.base_ok:
                return {
                    "args": argv,
                    "returncode": 128,
                    "stdout": "",
                    "stderr": "fatal: Needed a single revision\n",
                    "timedOut": False,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": "abc123\n",
                "stderr": "",
                "timedOut": False,
            }

        if "status" in argv and "--porcelain" in argv:
            if self.dirty and "-C" not in argv:
                return {
                    "args": argv,
                    "returncode": 0,
                    "stdout": " M dirty.txt\n",
                    "stderr": "",
                    "timedOut": False,
                }
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.porcelain if "-C" in argv else "",
                "stderr": "",
                "timedOut": False,
            }

        if "worktree" in argv and "add" in argv:
            if not self.worktree_add_ok:
                return {
                    "args": argv,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "fatal: worktree add failed\n",
                    "timedOut": False,
                }
            path = None
            if "-b" in argv:
                i = argv.index("-b")
                if i + 2 < len(argv):
                    path = Path(argv[i + 2])
            else:
                add_i = argv.index("add")
                if add_i + 1 < len(argv):
                    path = Path(argv[add_i + 1])
            if path is not None and self.create_target:
                path.mkdir(parents=True, exist_ok=True)
                (path / ".git").write_text("gitdir: mock\n", encoding="utf-8")
            return {
                "args": argv,
                "returncode": 0,
                "stdout": f"Preparing worktree at {path}\n",
                "stderr": "",
                "timedOut": False,
            }

        if "rev-parse" in argv and "--abbrev-ref" in argv:
            branch = self.existing_branch or "grok/unknown"
            return {
                "args": argv,
                "returncode": 0,
                "stdout": f"{branch}\n",
                "stderr": "",
                "timedOut": False,
            }

        if "diff" in argv and "--name-only" in argv:
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.name_only,
                "stderr": "",
                "timedOut": False,
            }

        if "diff" in argv and "--stat" in argv:
            return {
                "args": argv,
                "returncode": 0,
                "stdout": self.diff_stat,
                "stderr": "",
                "timedOut": False,
            }

        return {
            "args": argv,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
        }


class MockSubprocess:
    def __init__(
        self,
        *,
        ok: bool = True,
        stdout: str | None = None,
        missing: bool = False,
        timed_out: bool = False,
    ) -> None:
        self.ok = ok
        self.stdout = stdout if stdout is not None else json.dumps(
            {"summary": "done", "turns_used": 3}
        )
        self.missing = missing
        self.timed_out = timed_out
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: list[str] | tuple[str, ...],
        cwd: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        if self.missing:
            return {
                "args": argv,
                "returncode": 127,
                "stdout": "",
                "stderr": "not found",
                "timedOut": False,
                "missing": True,
            }
        if self.timed_out:
            return {
                "args": argv,
                "returncode": 124,
                "stdout": "",
                "stderr": "timeout",
                "timedOut": True,
            }
        return {
            "args": argv,
            "returncode": 0 if self.ok else 1,
            "stdout": self.stdout,
            "stderr": "",
            "timedOut": False,
        }


def _assert_argv_headless_and_safe(test: unittest.TestCase, argv: list[str]) -> None:
    """Behavior checks on shipped argv (R1/B2/R3)."""
    test.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
    test.assertNotIn("bypassPermissions", argv)
    test.assertTrue(
        guard.argv_has_headless_interface(argv),
        f"argv missing headless interface: {argv}",
    )
    test.assertIn("--cwd", argv)
    test.assertIn("--output-format", argv)
    test.assertIn("json", argv)
    test.assertIn("--permission-mode", argv)
    mode_i = argv.index("--permission-mode")
    test.assertNotEqual(argv[mode_i + 1], guard.BYPASS_PERMISSIONS_MODE)
    # Must not be bare interactive positional alone: last flag-pair is --single <goal>
    test.assertIn(guard.HEADLESS_SINGLE_LONG, argv)
    si = argv.index(guard.HEADLESS_SINGLE_LONG)
    test.assertLess(si + 1, len(argv))
    test.assertFalse(str(argv[si + 1]).startswith("--"))


class GuardTests(unittest.TestCase):
    def test_normalize_lane_accepts_slug_and_prefix(self) -> None:
        self.assertEqual(guard.normalize_lane("my-slice"), "grok/my-slice")
        self.assertEqual(guard.normalize_lane("grok/my-slice"), "grok/my-slice")

    def test_reserved_lane_reject_dev_master_main(self) -> None:
        for name in ("dev", "master", "main", "grok/dev", "grok/master", "grok/main"):
            with self.subTest(name=name):
                with self.assertRaises(guard.GuardError) as ctx:
                    guard.normalize_lane(name)
                self.assertEqual(ctx.exception.code, "LANE_RESERVED")

    def test_empty_and_invalid_lane_reject(self) -> None:
        for name in ("", "  ", None, "UPPER", "has space", "a/b/c", "-bad"):
            with self.subTest(name=name):
                with self.assertRaises(guard.GuardError):
                    guard.normalize_lane(name)  # type: ignore[arg-type]

    def test_enforce_bounds_hard_cap(self) -> None:
        self.assertEqual(guard.enforce_bounds(10), 10)
        self.assertEqual(guard.enforce_bounds(None), guard.HARD_CAP_MAX_TURNS)
        with self.assertRaises(guard.GuardError) as ctx:
            guard.enforce_bounds(guard.HARD_CAP_MAX_TURNS + 1)
        self.assertEqual(ctx.exception.code, "MAX_TURNS_CAP")
        with self.assertRaises(guard.GuardError):
            guard.enforce_bounds(0)

    def test_profile_denies_push_merge_and_no_interpreters(self) -> None:
        profile = guard.build_permission_profile(plan_only=False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
        self.assertTrue(guard.profile_denies_cwd_escape(profile))
        self.assertFalse(guard.profile_allows_interpreters(profile))
        # Structural: exact deny rules present
        self.assertIn("Bash(git push*)", profile["deny"])
        self.assertIn("Bash(git merge*)", profile["deny"])
        self.assertIn("Write([A-Za-z]:/**)", profile["deny"])
        self.assertIn("Edit([A-Za-z]:/**)", profile["deny"])
        self.assertIn("Read(~/.grok/**)", profile["deny"])
        self.assertIn("Bash(rm -rf*)", profile["deny"])
        # R2: interpreters not in allow
        allow = profile["allow"]
        self.assertNotIn("Bash(python*)", allow)
        self.assertNotIn("Bash(pytest*)", allow)
        self.assertNotIn("Bash(npm*)", allow)

    def test_plan_only_read_only_profile(self) -> None:
        profile = guard.build_permission_profile(plan_only=True)
        deny = profile["deny"]
        tools = profile["disallowed_tools"]
        self.assertIn("Write(**)", deny)
        self.assertIn("Edit(**)", deny)
        self.assertIn("Bash(*)", deny)
        self.assertIn("Write", tools)
        self.assertIn("Bash", tools)
        self.assertEqual(profile["permission_mode"], guard.PERMISSION_MODE_PLAN)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))

    def test_argv_uses_headless_single_not_positional(self) -> None:
        profile = guard.build_permission_profile(False)
        goal = "implement feature X"
        argv = guard.build_grok_argv(
            goal,
            "/tmp/wt",
            profile,
            12,
            model="grok-4",
            plan_only=False,
        )
        _assert_argv_headless_and_safe(self, argv)
        self.assertIn("--max-turns", argv)
        self.assertIn("12", argv)
        self.assertIn("--no-plan", argv)
        self.assertIn("--deny", argv)
        self.assertIn("--disallowed-tools", argv)
        self.assertIn("--model", argv)
        # Goal is value of --single, not a bare trailing interactive prompt only
        si = argv.index("--single")
        self.assertEqual(argv[si + 1], goal)
        # No bare goal without headless flag: if goal appears, it must be after --single
        goal_positions = [i for i, a in enumerate(argv) if a == goal]
        self.assertEqual(goal_positions, [si + 1])
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")

    def test_argv_plan_only_omits_no_plan_uses_plan_mode(self) -> None:
        profile = guard.build_permission_profile(True)
        argv = guard.build_grok_argv(
            "plan only",
            "/tmp/wt",
            profile,
            5,
            plan_only=True,
        )
        self.assertNotIn("--no-plan", argv)
        self.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        self.assertTrue(guard.argv_has_headless_interface(argv))

    def test_assert_argv_safe_blocks_smuggled_always_approve(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(["grok", "--always-approve", "x"])
        self.assertEqual(ctx.exception.code, "ALWAYS_APPROVE_FORBIDDEN")

    def test_assert_argv_safe_requires_headless(self) -> None:
        # Bare interactive positional is rejected
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(
                [
                    "grok",
                    "--cwd",
                    "/wt",
                    "--output-format",
                    "json",
                    "--permission-mode",
                    "dontAsk",
                    "interactive goal only",
                ]
            )
        self.assertEqual(ctx.exception.code, "ARGV_NOT_HEADLESS")

    def test_assert_argv_safe_blocks_bypass_permissions(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(
                [
                    "grok",
                    "--cwd",
                    "/wt",
                    "--output-format",
                    "json",
                    "--permission-mode",
                    "bypassPermissions",
                    "--single",
                    "x",
                ]
            )
        self.assertEqual(ctx.exception.code, "PERMISSION_MODE_FORBIDDEN")

    def test_build_argv_rejects_interpreter_allow(self) -> None:
        profile = {
            "allow": ["Bash(python*)"],
            "deny": [],
            "disallowed_tools": [],
            "permission_mode": "dontAsk",
        }
        with self.assertRaises(guard.GuardError) as ctx:
            guard.build_grok_argv("g", "/wt", profile, 3)
        self.assertEqual(ctx.exception.code, "ALLOW_INTERPRETER_FORBIDDEN")

    def test_validate_grok_bin_client_forbidden(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_grok_bin("C:/evil/python.exe", from_client=True)
        self.assertEqual(ctx.exception.code, "GROK_BIN_CLIENT_FORBIDDEN")

    def test_validate_grok_bin_rejects_python_path(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.validate_grok_bin("C:/Python/python.exe", from_client=False)
        self.assertEqual(ctx.exception.code, "GROK_BIN_INVALID")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prepare_rejects_target_inside_repo(self) -> None:
        git = MockGit()
        inside = self.repo / "nested-lanes"
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=inside,
            git_runner=git,
            require_clean_base=True,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "WORKTREE_INSIDE_REPO")
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(add_calls, [])

    def test_prepare_rejects_dirty_base(self) -> None:
        git = MockGit(dirty=True)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_DIRTY")
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(add_calls, [])

    def test_prepare_rejects_missing_git(self) -> None:
        git = MockGit(version_ok=False)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GIT_MISSING")

    def test_prepare_rejects_unreachable_base(self) -> None:
        git = MockGit(base_ok=False)
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="safe-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_UNREACHABLE")

    def test_prepare_rejects_reserved_lane(self) -> None:
        git = MockGit()
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="dev",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANE_RESERVED")

    def test_prepare_success_creates_external_worktree(self) -> None:
        git = MockGit()
        result = runner.prepare_worktree(
            repo_root=self.repo,
            lane="demo-slice",
            lanes_parent=self.lanes,
            git_runner=git,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/demo-slice")
        self.assertEqual(result.get("branch"), "grok/demo-slice")
        wt = Path(result["worktree_path"])
        self.assertTrue(wt.exists())
        self.assertFalse(runner.is_path_inside(wt, self.repo))
        add_calls = [c for c in git.calls if "worktree" in c and "add" in c]
        self.assertEqual(len(add_calls), 1)
        self.assertIn("-b", add_calls[0])
        self.assertIn("grok/demo-slice", add_calls[0])

    def test_run_delegation_argv_safe_and_no_spawn_on_missing(self) -> None:
        sp = MockSubprocess(missing=True)
        result = runner.run_delegation(
            goal="do thing",
            worktree_path=self.lanes / "x",
            max_turns=5,
            subprocess_runner=sp,
            which=lambda _n: None,
            grok_bin="grok",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_MISSING")
        self.assertEqual(sp.calls, [])

    def test_run_delegation_never_passes_always_approve_and_is_headless(self) -> None:
        sp = MockSubprocess(ok=True)
        (self.lanes / "wt").mkdir()
        result = runner.run_delegation(
            goal="implement safely",
            worktree_path=self.lanes / "wt",
            max_turns=7,
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(sp.calls), 1)
        argv = sp.calls[0]
        _assert_argv_headless_and_safe(self, argv)
        self.assertIn("--max-turns", argv)
        self.assertIn("7", argv)

    def test_run_delegation_rejects_over_cap_before_spawn(self) -> None:
        sp = MockSubprocess(ok=True)
        result = runner.run_delegation(
            goal="x",
            worktree_path=self.lanes / "wt",
            max_turns=guard.HARD_CAP_MAX_TURNS + 50,
            subprocess_runner=sp,
            which=lambda n: n,
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "MAX_TURNS_CAP")
        self.assertEqual(sp.calls, [])

    def test_run_delegation_rejects_invalid_grok_bin_before_spawn(self) -> None:
        sp = MockSubprocess(ok=True)
        result = runner.run_delegation(
            goal="x",
            worktree_path=self.lanes / "wt",
            max_turns=3,
            subprocess_runner=sp,
            which=lambda n: n,
            grok_bin="python.exe",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_BIN_INVALID")
        self.assertEqual(sp.calls, [])

    def test_collect_diff_no_full_patch(self) -> None:
        git = MockGit()
        out = runner.collect_diff(self.lanes / "wt", git_runner=git)
        self.assertTrue(out.get("ok"))
        self.assertIn("guard.py", " ".join(out["changed_files"]))
        self.assertIn("file changed", out["diffstat"])
        for c in git.calls:
            if "diff" in c:
                self.assertTrue("--stat" in c or "--name-only" in c)

    def test_delegate_happy_path_mocked(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        result = runner.delegate(
            goal="add tests",
            lane="happy-path",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            max_turns=4,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/happy-path")
        self.assertEqual(result.get("branch"), "grok/happy-path")
        self.assertIn("worktree_path", result)
        self.assertIsInstance(result.get("changed_files"), list)
        self.assertIn("diffstat", result)
        for c in git.calls:
            self.assertNotIn("push", c)
            self.assertNotIn("merge", c)
        self.assertEqual(len(sp.calls), 1)
        _assert_argv_headless_and_safe(self, sp.calls[0])

    def test_delegate_fail_closed_dirty_does_not_spawn(self) -> None:
        git = MockGit(dirty=True)
        sp = MockSubprocess(ok=True)
        result = runner.delegate(
            goal="should not run",
            lane="blocked",
            repo_root=self.repo,
            lanes_parent=self.lanes,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "BASE_DIRTY")
        self.assertEqual(sp.calls, [])

    def test_default_git_runner_rejects_push_verb(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            runner._reject_forbidden_git_args(["push", "origin", "dev"])
        self.assertEqual(ctx.exception.code, "GIT_VERB_FORBIDDEN")

    def test_default_git_runner_rejects_merge_verb(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            runner._reject_forbidden_git_args(["merge", "dev"])
        self.assertEqual(ctx.exception.code, "GIT_VERB_FORBIDDEN")

    def test_subprocess_rejects_always_approve(self) -> None:
        with self.assertRaises(guard.GuardError):
            runner._reject_always_approve(["grok", "--always-approve"])


class AuditTests(unittest.TestCase):
    def test_emit_allowlisted_fields_only(self) -> None:
        buf = io.StringIO()
        event = audit_mod.build_delegation_audit(
            principal="local-dev",
            tool="grok_delegate",
            lane="grok/x",
            base_ref="origin/dev",
            cwd="/lanes/x",
            turns_used=3,
            outcome="ok",
            goal="super secret goal text that must not appear",
            changed_file_count=2,
        )
        event["goal"] = "super secret goal text that must not appear"
        event["diff"] = "+++ full patch"
        event["api_key"] = "should-be-dropped"
        safe = audit_mod.emit(event, stream=buf)
        line = buf.getvalue()
        self.assertNotIn("super secret", line)
        self.assertNotIn("full patch", line)
        self.assertNotIn("should-be-dropped", line)
        self.assertNotIn("~/.grok", line)
        self.assertIn("goal_chars", line)
        self.assertIn("goal_sha256_8", line)
        self.assertEqual(safe["principal"], "local-dev")
        self.assertEqual(safe["tool"], "grok_delegate")
        self.assertEqual(safe["outcome"], "ok")

    def test_sanitize_rejects_auth_path_leak(self) -> None:
        with self.assertRaises(audit_mod.AuditError):
            audit_mod.sanitize_event(
                {
                    "principal": "x",
                    "tool": "t",
                    "lane": "l",
                    "base_ref": "b",
                    "cwd": "/home/user/.grok/auth.json",
                    "turns_used": 1,
                    "outcome": "ok",
                }
            )

    def test_no_secret_patterns_in_emit(self) -> None:
        buf = io.StringIO()
        audit_mod.emit(
            {
                "principal": "local-dev",
                "tool": "grok_delegate",
                "lane": "grok/a",
                "base_ref": "origin/dev",
                "cwd": "/tmp/wt",
                "turns_used": 1,
                "outcome": "error",
                "error": "GROK_MISSING",
            },
            stream=buf,
        )
        data = json.loads(buf.getvalue())
        for key in data:
            self.assertIn(key, audit_mod.ALLOWED_FIELDS | {"ts"})


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.lanes = Path(self.tmp.name) / "pcp-lanes"
        self.lanes.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handle_tool_call_delegate_mocked(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        buf = io.StringIO()
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "ship feature",
                "lane": "mcp-demo",
                "max_turns": 6,
                "lanes_parent": str(self.lanes),
            },
            repo_root=self.repo,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=buf,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("lane"), "grok/mcp-demo")
        self.assertIn("diffstat", result)
        audit_line = buf.getvalue()
        self.assertIn("grok_delegate", audit_line)
        self.assertNotIn("ship feature", audit_line)
        self.assertEqual(len(sp.calls), 1)
        _assert_argv_headless_and_safe(self, sp.calls[0])

    def test_handle_tool_plan_sets_plan_only(self) -> None:
        git = MockGit()
        sp = MockSubprocess(ok=True)
        result = server.handle_tool_call(
            "grok_delegate_plan",
            {
                "goal": "plan the work",
                "lane": "plan-demo",
                "lanes_parent": str(self.lanes),
            },
            repo_root=self.repo,
            git_runner=git,
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(sp.calls), 1)
        argv = sp.calls[0]
        self.assertNotIn("--no-plan", argv)
        self.assertNotIn("--always-approve", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        _assert_argv_headless_and_safe(self, argv)

    def test_handle_jsonrpc_tools_list(self) -> None:
        resp = server.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("grok_delegate", names)
        self.assertIn("grok_delegate_plan", names)
        schema = resp["result"]["tools"][0]["inputSchema"]
        self.assertNotIn("grok_bin", schema.get("properties", {}))
        self.assertEqual(schema.get("additionalProperties"), False)

    def test_handle_jsonrpc_initialize(self) -> None:
        resp = server.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {},
            }
        )
        assert resp is not None
        self.assertEqual(resp["result"]["serverInfo"]["name"], "grok-delegate")

    def test_reserved_lane_via_tool(self) -> None:
        result = server.handle_tool_call(
            "grok_delegate",
            {"goal": "nope", "lane": "master", "lanes_parent": str(self.lanes)},
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=MockSubprocess(),
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANE_RESERVED")

    def test_client_grok_bin_rejected(self) -> None:
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "nope",
                "lane": "x",
                "lanes_parent": str(self.lanes),
                "grok_bin": "C:/Windows/System32/cmd.exe",
            },
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=MockSubprocess(),
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_BIN_CLIENT_FORBIDDEN")

    def test_client_repo_root_mismatch_rejected_when_env_set(self) -> None:
        sp = MockSubprocess(ok=True)
        with mock.patch.dict(
            os.environ,
            {"GROK_DELEGATE_REPO_ROOT": str(self.repo.resolve())},
            clear=False,
        ):
            result = server.handle_tool_call(
                "grok_delegate",
                {
                    "goal": "nope",
                    "lane": "x",
                    "lanes_parent": str(self.lanes),
                    "repo_root": str(Path(self.tmp.name) / "other"),
                },
                repo_root=self.repo,
                git_runner=MockGit(),
                subprocess_runner=sp,
                which=lambda n: "/mock/grok",
                audit_stream=io.StringIO(),
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "REPO_ROOT_UNTRUSTED")
        self.assertEqual(sp.calls, [])

    def test_client_lanes_parent_inside_repo_rejected(self) -> None:
        sp = MockSubprocess(ok=True)
        result = server.handle_tool_call(
            "grok_delegate",
            {
                "goal": "nope",
                "lane": "x",
                "lanes_parent": str(self.repo / "inside"),
            },
            repo_root=self.repo,
            git_runner=MockGit(),
            subprocess_runner=sp,
            which=lambda n: "/mock/grok",
            audit_stream=io.StringIO(),
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "LANES_PARENT_INSIDE_REPO")
        self.assertEqual(sp.calls, [])


class StaticBoundaryTests(unittest.TestCase):
    """Structural checks on shipped sources (no product surface / no push)."""

    def test_no_src_imports(self) -> None:
        for path in _PKG.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"from\s+src\b|import\s+src\b")

    def test_no_push_merge_helpers_in_runner(self) -> None:
        text = (_PKG / "runner.py").read_text(encoding="utf-8")
        self.assertNotIn("def push", text)
        self.assertNotIn("def merge", text)
        self.assertNotIn('["push"', text)
        self.assertNotIn('["merge"', text)
        self.assertNotIn('git", "push"', text)
        self.assertNotIn('git", "merge"', text)
        self.assertIn("_FORBIDDEN_GIT_VERBS", text)
        self.assertIn("GIT_VERB_FORBIDDEN", text)

    def test_never_reads_auth_json(self) -> None:
        guard_text = (_PKG / "guard.py").read_text(encoding="utf-8")
        self.assertIn("auth.json", guard_text)
        self.assertIn("_DENY_AUTH", guard_text)
        for path in (
            _PKG / "runner.py",
            _PKG / "server.py",
            _PKG / "audit.py",
            _PKG / "guard.py",
        ):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("read_auth", src)
            self.assertNotIn("load_auth", src)
            self.assertNotIn('Path.home() / ".grok"', src)
            self.assertNotIn("Path.home()/'.grok'", src)
            self.assertNotIn('open(os.path.expanduser("~/.grok', src)

    def test_package_modules_exist(self) -> None:
        for name in ("guard.py", "runner.py", "audit.py", "server.py", "README.md"):
            self.assertTrue((_PKG / name).is_file(), name)

    def test_schema_has_no_grok_bin(self) -> None:
        self.assertNotIn("grok_bin", server._INPUT_SCHEMA["properties"])
        self.assertFalse(server._INPUT_SCHEMA.get("additionalProperties", True))


class AdversarialBypassMapTests(unittest.TestCase):
    """Behavior-level bypass map (R4) — drives shipped functions, not theater."""

    def test_vector_escape_cwd_profile_has_windows_and_unc_denies(self) -> None:
        # file:symbol guard.build_permission_profile / profile_denies_cwd_escape
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_cwd_escape(profile))
        deny = profile["deny"]
        self.assertIn("Write([A-Za-z]:/**)", deny)
        self.assertIn("Edit([A-Za-z]:/**)", deny)
        self.assertIn("Write(//**)", deny)
        # Relative ../ is NOT claimed closed — helper must not rely on //** alone.
        # Build argv and ensure permission-mode + cwd are present (real path).
        argv = guard.build_grok_argv("g", r"C:\lanes\wt", profile, 3)
        self.assertIn("--cwd", argv)
        self.assertIn(r"C:\lanes\wt", argv)
        self.assertIn("--permission-mode", argv)
        self.assertNotEqual(
            argv[argv.index("--permission-mode") + 1],
            "bypassPermissions",
        )

    def test_vector_main_tree_closed_by_prepare(self) -> None:
        # file:symbol runner.prepare_worktree / is_path_inside
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            git = MockGit()
            result = runner.prepare_worktree(
                repo_root=repo,
                lane="x",
                lanes_parent=repo / "inside",
                git_runner=git,
            )
            self.assertEqual(result.get("error"), "WORKTREE_INSIDE_REPO")

    def test_vector_reserved_lane_closed(self) -> None:
        # file:symbol guard.normalize_lane
        with self.assertRaises(guard.GuardError):
            guard.normalize_lane("dev")

    def test_vector_push_merge_closed_by_profile_and_runner(self) -> None:
        # file:symbol guard.build_permission_profile + runner._reject_forbidden_git_args
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
        argv = guard.build_grok_argv("g", "/wt", profile, 3)
        # Deny rules are actually on argv (behavior of builder), not just profile dict.
        deny_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--deny"]
        self.assertTrue(any(v.startswith("Bash(git push") for v in deny_values))
        self.assertTrue(any(v.startswith("Bash(git merge") for v in deny_values))
        with self.assertRaises(guard.GuardError):
            runner._reject_forbidden_git_args(["push", "origin", "HEAD"])

    def test_vector_unbounded_turns_closed(self) -> None:
        # file:symbol guard.enforce_bounds
        with self.assertRaises(guard.GuardError) as ctx:
            guard.enforce_bounds(10_000)
        self.assertEqual(ctx.exception.code, "MAX_TURNS_CAP")

    def test_vector_cred_leak_closed_by_audit(self) -> None:
        # file:symbol audit.sanitize_event / emit
        with self.assertRaises(audit_mod.AuditError):
            audit_mod.sanitize_event(
                {
                    "principal": "p",
                    "tool": "t",
                    "lane": "l",
                    "base_ref": "b",
                    "cwd": "C:/Users/x/.grok/auth.json",
                    "turns_used": 1,
                    "outcome": "ok",
                }
            )

    def test_vector_destructive_shell_denied_and_interpreters_not_allowed(self) -> None:
        # file:symbol guard.build_permission_profile / profile_allows_interpreters
        profile = guard.build_permission_profile(False)
        deny = profile["deny"]
        self.assertIn("Bash(rm -rf*)", deny)
        self.assertFalse(guard.profile_allows_interpreters(profile))
        # Builder rejects smuggling interpreters into allow at argv construction time.
        bad = {
            "allow": list(profile["allow"]) + ["Bash(python*)"],
            "deny": list(profile["deny"]),
            "disallowed_tools": list(profile["disallowed_tools"]),
            "permission_mode": profile["permission_mode"],
        }
        with self.assertRaises(guard.GuardError) as ctx:
            guard.build_grok_argv("g", "/wt", bad, 3)
        self.assertEqual(ctx.exception.code, "ALLOW_INTERPRETER_FORBIDDEN")

    def test_vector_always_approve_smuggle_closed(self) -> None:
        # file:symbol guard.build_grok_argv / assert_argv_safe / runner._reject_always_approve
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("g", "/wt", profile, 3)
        self.assertNotIn("--always-approve", argv)
        with self.assertRaises(guard.GuardError):
            guard.assert_argv_safe(["grok", "--always-approve"])

    def test_vector_headless_required_on_shipped_builder(self) -> None:
        # file:symbol guard.build_grok_argv / argv_has_headless_interface
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("coding goal", "/wt", profile, 4)
        self.assertTrue(guard.argv_has_headless_interface(argv))
        self.assertIn("--single", argv)
        # Failure mode: bare interactive argv fails assert_argv_safe
        bare = ["grok", "--cwd", "/wt", "--output-format", "json", "--permission-mode", "dontAsk", "goal"]
        self.assertFalse(guard.argv_has_headless_interface(bare))
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(bare)
        self.assertEqual(ctx.exception.code, "ARGV_NOT_HEADLESS")

    def test_vector_client_grok_bin_closed(self) -> None:
        # file:symbol server.resolve_server_grok_bin / guard.validate_grok_bin
        with self.assertRaises(guard.GuardError) as ctx:
            server.resolve_server_grok_bin({"grok_bin": "C:/evil/cmd.exe"})
        self.assertEqual(ctx.exception.code, "GROK_BIN_CLIENT_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
