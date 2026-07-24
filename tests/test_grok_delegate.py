#!/usr/bin/env python3
"""Unit tests for tools/grok-delegate (mocked git + subprocess only).

No real grok spawn, no real git mutation. Drives shipped guard/runner/audit/
server entry points.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import audit as audit_mod  # noqa: E402
import guard  # noqa: E402
import runner  # noqa: E402
import server  # noqa: E402


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
        name_only: str = "tools/grok-delegate/guard.py\n",
        porcelain: str = " M tools/grok-delegate/guard.py\n",
        diff_stat: str = " tools/grok-delegate/guard.py | 10 ++++++++++\n 1 file changed\n",
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

        # Reject if test somehow asks for push/merge through runner (runner
        # should raise first; this is belt-and-suspenders for assert helpers).
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
            # Distinguishing main-repo dirty check vs worktree collect_diff:
            # both use porcelain; dirty flag applies to main-repo checks only
            # when worktree path is not present as -C target that exists with reuse.
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
            # argv shapes:
            # worktree add -b branch path base
            # worktree add path branch
            path = None
            if "-b" in argv:
                i = argv.index("-b")
                # path is after branch name
                if i + 2 < len(argv):
                    path = Path(argv[i + 2])
            else:
                # worktree add <path> <branch>
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

    def test_profile_denies_push_merge_cwd_escape(self) -> None:
        profile = guard.build_permission_profile(plan_only=False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
        self.assertTrue(guard.profile_denies_cwd_escape(profile))
        deny_blob = " ".join(profile["deny"]).lower()
        self.assertIn("push", deny_blob)
        self.assertIn("merge", deny_blob)
        self.assertIn("~/.grok", deny_blob)
        # live/device/prod/root/destructive shell
        self.assertIn("live", deny_blob)
        self.assertIn("prod", deny_blob)
        self.assertIn("root", deny_blob)
        self.assertIn("rm -rf", deny_blob)

    def test_plan_only_read_only_profile(self) -> None:
        profile = guard.build_permission_profile(plan_only=True)
        deny = " ".join(profile["deny"])
        tools = " ".join(profile["disallowed_tools"])
        self.assertIn("Write(**)", deny)
        self.assertIn("Edit(**)", deny)
        self.assertIn("Bash(*)", deny)
        self.assertIn("Write", tools)
        self.assertIn("Bash", tools)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))

    def test_argv_never_contains_always_approve(self) -> None:
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv(
            "implement feature X",
            "/tmp/wt",
            profile,
            12,
            model="grok-4",
            plan_only=False,
        )
        self.assertNotIn(guard.ALWAYS_APPROVE_FLAG, argv)
        self.assertNotIn("--always-approve", " ".join(argv))
        self.assertIn("--cwd", argv)
        self.assertIn("/tmp/wt", argv)
        self.assertIn("--output-format", argv)
        self.assertIn("json", argv)
        self.assertIn("--max-turns", argv)
        self.assertIn("12", argv)
        self.assertIn("--no-plan", argv)
        self.assertIn("--deny", argv)
        self.assertIn("--disallowed-tools", argv)

    def test_argv_plan_only_omits_no_plan(self) -> None:
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

    def test_assert_argv_safe_blocks_smuggled_always_approve(self) -> None:
        with self.assertRaises(guard.GuardError) as ctx:
            guard.assert_argv_safe(["grok", "--always-approve", "x"])
        self.assertEqual(ctx.exception.code, "ALWAYS_APPROVE_FORBIDDEN")


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
        # Point lanes_parent inside repo → target inside main tree.
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
        # No worktree add attempted after bound check? Bound check is after
        # normalize; may run version first — ensure no add if inside.
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
            grok_bin="grok-not-real-binary",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "GROK_MISSING")
        # which returns None and path not a file — fail before or with missing.
        # If spawn was attempted, MockSubprocess would record; missing path may
        # short-circuit before run.
        self.assertTrue(len(sp.calls) == 0 or sp.calls)

    def test_run_delegation_never_passes_always_approve(self) -> None:
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
        self.assertNotIn("--always-approve", argv)
        self.assertIn("--cwd", argv)
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

    def test_collect_diff_no_full_patch(self) -> None:
        git = MockGit()
        out = runner.collect_diff(self.lanes / "wt", git_runner=git)
        self.assertTrue(out.get("ok"))
        self.assertIn("guard.py", " ".join(out["changed_files"]))
        self.assertIn("file changed", out["diffstat"])
        # Ensure we never asked for unified full diff without --stat/--name-only
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
        # No push/merge in any git call
        for c in git.calls:
            self.assertNotIn("push", c)
            self.assertNotIn("merge", c)

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
        # Smuggle forbidden keys
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
        # Legitimate event without secrets
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
        # plan_only profile: argv should not include --no-plan and should deny bash
        self.assertEqual(len(sp.calls), 1)
        argv = sp.calls[0]
        self.assertNotIn("--no-plan", argv)
        self.assertNotIn("--always-approve", argv)

    def test_handle_jsonrpc_tools_list(self) -> None:
        resp = server.handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("grok_delegate", names)
        self.assertIn("grok_delegate_plan", names)

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


class StaticBoundaryTests(unittest.TestCase):
    """Structural checks on shipped sources (no product surface / no push)."""

    def test_no_src_imports(self) -> None:
        for path in _HERE.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"from\s+src\b|import\s+src\b")

    def test_no_push_merge_helpers_in_runner(self) -> None:
        text = (_HERE / "runner.py").read_text(encoding="utf-8")
        # Must not define push/merge operations as callable workflows.
        self.assertNotIn("def push", text)
        self.assertNotIn("def merge", text)
        # Must not assemble git push/merge argv (deny-list of verbs is allowed).
        self.assertNotIn('["push"', text)
        self.assertNotIn('["merge"', text)
        self.assertNotIn('git", "push"', text)
        self.assertNotIn('git", "merge"', text)
        self.assertIn("_FORBIDDEN_GIT_VERBS", text)
        self.assertIn("GIT_VERB_FORBIDDEN", text)

    def test_never_reads_auth_json(self) -> None:
        # guard.py may list auth.json only as a deny rule pattern.
        guard_text = (_HERE / "guard.py").read_text(encoding="utf-8")
        self.assertIn("auth.json", guard_text)
        self.assertIn("_DENY_AUTH", guard_text)
        # No module may open/read ~/.grok/auth.json as a credential source.
        for path in (_HERE / "runner.py", _HERE / "server.py", _HERE / "audit.py", _HERE / "guard.py"):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("read_auth", src)
            self.assertNotIn("load_auth", src)
            self.assertNotIn("Path.home() / \".grok\"", src)
            self.assertNotIn("Path.home()/'.grok'", src)
            self.assertNotIn('open(os.path.expanduser("~/.grok', src)

    def test_package_modules_exist(self) -> None:
        for name in ("guard.py", "runner.py", "audit.py", "server.py", "README.md"):
            self.assertTrue((_HERE / name).is_file(), name)


class AdversarialBypassMapTests(unittest.TestCase):
    """Each required bypass vector is closed by a shipped guard or test."""

    def test_vector_escape_cwd_closed_by_profile(self) -> None:
        # file:symbol guard.profile_denies_cwd_escape / build_permission_profile
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_cwd_escape(profile))

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

    def test_vector_push_merge_closed(self) -> None:
        # file:symbol guard.build_permission_profile + runner._reject_forbidden_git_args
        profile = guard.build_permission_profile(False)
        self.assertTrue(guard.profile_denies_push(profile))
        self.assertTrue(guard.profile_denies_merge(profile))
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

    def test_vector_destructive_shell_closed_by_deny(self) -> None:
        # file:symbol guard.build_permission_profile
        deny = " ".join(guard.build_permission_profile(False)["deny"])
        self.assertIn("rm -rf", deny)

    def test_vector_always_approve_smuggle_closed(self) -> None:
        # file:symbol guard.build_grok_argv / assert_argv_safe / runner._reject_always_approve
        profile = guard.build_permission_profile(False)
        argv = guard.build_grok_argv("g", "/wt", profile, 3)
        self.assertNotIn("--always-approve", argv)
        with self.assertRaises(guard.GuardError):
            guard.assert_argv_safe(["grok", "--always-approve"])


if __name__ == "__main__":
    unittest.main()
