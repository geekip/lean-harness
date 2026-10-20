"""初始化与显式文档迁移的跨平台黑箱回归；只操作临时工程。"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
INIT = SKILL / "scripts/init_project.py"
MIGRATE = SKILL / "scripts/migrate_project.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and ".git" not in path.parts
    }


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="lean-harness-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "中文 project"

    def invoke(self, script: Path, *args: str, expected: int = 0):
        process = subprocess.run(
            [sys.executable, "-B", str(script), *args],
            text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        return process

    def init(self, *args: str, project_type: str = "code", expected: int = 0):
        return self.invoke(
            INIT, "--target", str(self.root), "--project-type", project_type,
            "--requirements-mode", "qa", "--git-mode", "skip", "--json",
            *args, expected=expected,
        )

    def check(self) -> None:
        self.invoke(self.root / ".harness/tools/check.py")
        self.invoke(self.root / ".harness/tools/validate_traceability.py")
        self.invoke(self.root / ".harness/tools/sync_views.py")

    def mapping(self, moves: list[dict[str, str]]) -> Path:
        path = self.base / "mapping.json"
        write_json(path, {"moves": moves})
        return path

    def migrate(self, mapping: Path, *args: str, expected: int = 0):
        return self.invoke(
            MIGRATE, "--target", str(self.root), "--mapping", str(mapping),
            "--id", "migration-001", *args, expected=expected,
        )

    def test_code_layout_and_product_readme(self):
        result = json.loads(self.init().stdout)
        self.assertTrue(all(item["exit_code"] == 0 for item in result["checks"]))
        self.assertEqual(
            {p.name for p in (self.root / ".harness").iterdir() if p.is_dir()},
            {"document", "design", "plans", "progress", "validation", "history",
             "memory", "skills", "tools"},
        )
        for name in (".editorconfig", "AGENTS.md", "README.md", ".harness/traceability.json"):
            self.assertTrue((self.root / name).is_file(), name)
        for name in (".gitignore", "docs", "document", ".harness/guide",
                     ".harness/document/traceability.json"):
            self.assertFalse((self.root / name).exists(), name)
        self.assertNotIn(".harness", (self.root / "README.md").read_text(encoding="utf-8"))
        self.check()

    def test_non_code_never_creates_code_scaffolding(self):
        self.init(project_type="non-code")
        for name in (".gitignore", ".editorconfig", "README.md"):
            self.assertFalse((self.root / name).exists())
        self.assertTrue((self.root / "AGENTS.md").exists())
        self.check()
        before = files(self.root)
        self.init("--create-gitignore", project_type="non-code", expected=2)
        self.assertEqual(files(self.root), before)

    def test_optional_gitignore_is_generic(self):
        self.init("--create-gitignore")
        content = (self.root / ".gitignore").read_text(encoding="utf-8").lower()
        self.assertNotIn("harness", content)
        self.assertNotIn("agents.md", content)
        self.check()

    def test_existing_gitignore_bytes_always_preserved(self):
        for content in (b"", b"build/\r\n.harness/\r\n", b"\xff\x00raw\n"):
            with self.subTest(content=content):
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / ".gitignore"
                path.write_bytes(content)
                self.init("--create-gitignore", "--overwrite-existing")
                self.assertEqual(path.read_bytes(), content)

    def test_idempotent_preserves_existing_files(self):
        self.root.mkdir()
        for name in ("README.md", "AGENTS.md", ".editorconfig"):
            (self.root / name).write_bytes(b"User-owned file\r\n")
        self.init()
        before = files(self.root)
        result = json.loads(self.init().stdout)
        self.assertEqual(files(self.root), before)
        self.assertEqual(result["created"], [])
        self.assertEqual(result["overwritten"], [])
        self.check()

    def test_dry_run_does_not_create_target(self):
        result = json.loads(self.init("--dry-run", "--create-gitignore").stdout)
        self.assertEqual(result["status"], "预演")
        self.assertFalse(self.root.exists())
        self.assertIn(".harness/progress/dashboard.md", result["planned_files"])

    def test_old_layout_refused_even_with_overwrite(self):
        old = self.root / ".harness/document/traceability.json"
        write_json(old, {"schema_version": 1, "requirements": []})
        before = files(self.root)
        for flags in ((), ("--overwrite-existing",)):
            self.init(*flags, expected=2)
            self.assertEqual(files(self.root), before)

    def test_guide_without_metadata_is_also_legacy(self):
        path = self.root / ".harness/guide/dashboard.md"
        path.parent.mkdir(parents=True)
        path.write_text("旧看板\n", encoding="utf-8")
        before = files(self.root)
        self.init(expected=2)
        self.assertEqual(files(self.root), before)

    def test_overwrite_keeps_ledger_handoff_and_user_metadata(self):
        self.init()
        project_path = self.root / ".harness/project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project.update(summary="已确认概述", created_at="2020-01-01")
        write_json(project_path, project)
        trace_path = self.root / ".harness/traceability.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["state"]["action"] = "保留这项真实当前动作"
        write_json(trace_path, trace)
        before = {
            path: content for path, content in files(self.root).items()
            if path == ".harness/traceability.json" or path.startswith((
                ".harness/progress/handoffs/", ".harness/validation/", ".harness/history/",
            ))
        }
        self.init("--overwrite-existing")
        after = files(self.root)
        self.assertTrue(all(after[path] == content for path, content in before.items()))
        updated = json.loads(project_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["summary"], project["summary"])
        self.assertEqual(updated["created_at"], project["created_at"])
        self.check()

    def test_untrusted_helper_is_not_executed(self):
        self.init()
        helper = self.root / ".harness/tools/harnesslib.py"
        helper.write_text('raise RuntimeError("must never execute")\n', encoding="utf-8")
        result = json.loads(self.init().stdout)
        self.assertTrue(all(check["skipped"] for check in result["checks"]))
        self.assertEqual(helper.read_text(encoding="utf-8"),
                         'raise RuntimeError("must never execute")\n')

    def test_symlink_destination_refused_before_writes(self):
        external = self.base / "external"
        external.mkdir()
        self.root.mkdir()
        try:
            (self.root / ".harness").symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"本机不能创建测试软链接：{error}")
        self.init(expected=2)
        self.assertEqual(files(external), {})
        self.assertFalse((self.root / "README.md").exists())

    def test_malformed_existing_trace_returns_error_not_traceback(self):
        self.init()
        path = self.root / ".harness/traceability.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"] = None
        write_json(path, data)
        before = files(self.root)
        self.init(expected=2)
        self.assertEqual(files(self.root), before)

    def test_public_migration_archives_hashes_rewrites_links_and_trace(self):
        self.init()
        old = self.root / "notes/decision.md"
        old.parent.mkdir()
        old.write_text("# 用户决定\n\n参见 [产品](../README.md)。\n", encoding="utf-8")
        original = old.read_bytes()
        requirements = self.root / ".harness/document/requirements.md"
        requirements.write_text("# 用户需求\n\n[决定](../../notes/decision.md)\n", encoding="utf-8")
        trace_path = self.root / ".harness/traceability.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["decisions"] = [{"id": "D1", "source": "notes/decision.md", "status": "confirmed"}]
        write_json(trace_path, trace)
        mapping = self.mapping([{
            "old": "notes/decision.md", "new": ".harness/document/imported.md",
        }])
        before = files(self.root)
        self.migrate(mapping)
        self.assertEqual(files(self.root), before)
        self.migrate(mapping, "--apply")
        self.assertFalse(old.exists())
        destination = self.root / ".harness/document/imported.md"
        self.assertIn("(../../README.md)", destination.read_text(encoding="utf-8"))
        self.assertIn("(imported.md)", requirements.read_text(encoding="utf-8"))
        current = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual(current["decisions"][0]["source"], ".harness/document/imported.md")
        manifest = json.loads((self.root / ".harness/history/migration-001/migration.json")
                              .read_text(encoding="utf-8"))
        for item in manifest["moves"]:
            saved = self.root / item["snapshot"]
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), item["sha256"])
        saved = next(item for item in manifest["moves"] if item["old"] == "notes/decision.md")
        self.assertEqual((self.root / saved["snapshot"]).read_bytes(), original)
        self.assertEqual(len(current["handoffs"]), 2)
        self.invoke(self.root / ".harness/tools/sync_views.py", "--write")
        self.check()
        self.migrate(mapping, "--apply", expected=2)

    def test_legacy_strategy_can_move_to_absent_validation_destination(self):
        old = self.root / ".harness/document/testing.md"
        old.parent.mkdir(parents=True)
        old.write_text("# 测试策略\n", encoding="utf-8")
        mapping = self.mapping([{
            "old": ".harness/document/testing.md", "new": ".harness/validation/strategy.md",
        }])
        self.migrate(mapping, "--apply")
        self.assertFalse(old.exists())
        self.assertTrue((self.root / ".harness/validation/strategy.md").exists())

    def test_migration_rewrites_complex_links_and_active_indices(self):
        self.init()
        originals = {
            "notes/item(1).md": ".harness/document/item-renamed.md",
            "notes/a#b.md": ".harness/document/new#name.md",
            "notes/space name.md": ".harness/document/new space.md",
        }
        for old in originals:
            path = self.root / old
            path.parent.mkdir(exist_ok=True)
            path.write_text("# 章节\n", encoding="utf-8")
        links = self.root / ".harness/document/links.markdown"
        links.write_text(
            "# 活动引用\n\n"
            "[括号](../../notes/item(1).md?raw=1#章节)\n"
            "[哈希](../../notes/a%23b.md)\n"
            '[空格](<../../notes/space name.md> "标题")\n'
            "[引用][spec]\n\n[spec]: ../../notes/item(1).md\n"
            "`[代码不是链接](../../notes/item(1).md)`\n",
            encoding="utf-8",
        )
        targets = (
            ".harness/validation/strategy.md",
            ".harness/validation/README.md",
            ".harness/history/README.md",
            ".harness/progress/handoffs/README.md",
            ".harness/document/sources/README.md",
            ".harness/document/UPPER.MD",
        )
        for relative in targets:
            path = self.root / relative
            reference = posixpath.relpath("notes/item(1).md", posixpath.dirname(relative))
            previous = path.read_text(encoding="utf-8") if path.exists() else "# 索引\n"
            path.write_text(previous + f"\n[材料]({reference})\n", encoding="utf-8")
        raw = self.root / ".harness/document/sources/原文.md"
        raw.write_text("[原始失效链接不应改写](missing.md)\n", encoding="utf-8")
        original_raw = raw.read_bytes()
        mapping = self.mapping([{"old": old, "new": new} for old, new in originals.items()])
        self.migrate(mapping, "--apply")
        actual = links.read_text(encoding="utf-8")
        self.assertIn("(item-renamed.md?raw=1#章节)", actual)
        self.assertIn("(new%23name.md)", actual)
        self.assertIn('(<new%20space.md> "标题")', actual)
        self.assertIn("[spec]: item-renamed.md", actual)
        self.assertIn("`[代码不是链接](../../notes/item(1).md)`", actual)
        self.assertEqual(raw.read_bytes(), original_raw)
        self.invoke(self.root / ".harness/tools/sync_views.py", "--write")
        self.check()

    def test_migration_preserves_user_words_and_reports_text_review(self):
        self.init()
        old = self.root / "incoming/user.md"
        old.parent.mkdir()
        old.write_text("# 用户材料\n", encoding="utf-8")
        note = self.root / ".harness/document/decisions.md"
        words = "用户原话：请将 `incoming/user.md` 移到 `.harness/document/sources/user.md`。\n"
        note.write_text("# 用户决定\n\n" + words, encoding="utf-8")
        mapping = self.mapping([{
            "old": "incoming/user.md", "new": ".harness/document/sources/user.md",
        }])
        result = json.loads(self.migrate(mapping, "--apply").stdout)
        self.assertIn(words, note.read_text(encoding="utf-8"))
        self.assertIn({
            "path": ".harness/document/decisions.md", "old_path": "incoming/user.md",
        }, result["review_text_references"])
        self.invoke(self.root / ".harness/tools/sync_views.py", "--write")
        self.check()

    def test_migration_collisions_and_immutable_paths_refused(self):
        self.init()
        for old, new in (
            (".harness/document/requirements.md", ".harness/design/engineering.md"),
            (".harness/document/requirements.md", "../outside.md"),
            (".harness/document/requirements.md", ".harness/validation/T1/report.md"),
            (".harness/validation/strategy.md", ".harness/document/stolen.md"),
            ("./.harness/validation/strategy.md", ".harness/document/stolen.md"),
            (".harness/document/requirements.md", ".harness//history/stolen.md"),
            (".harness/document/requirements.md", "./.harness/project.json"),
            (".harness/document/requirements.md", ".harness/design/engineering.md/child.md"),
            (".harness/document/requirements.md", "docs/./alias.md"),
        ):
            with self.subTest(old=old, new=new):
                before = files(self.root)
                self.migrate(self.mapping([{"old": old, "new": new}]), "--apply", expected=2)
                self.assertEqual(files(self.root), before)

    @unittest.skipUnless(shutil.which("git"), "Git 不可用")
    def test_local_git_initialization_only_and_remote_configuration(self):
        result = self.invoke(
            INIT, "--target", str(self.root), "--project-type", "code",
            "--requirements-mode", "qa", "--git-mode", "init",
            "--remote-url", "https://example.invalid/not-contacted.git", "--json",
        )
        self.assertTrue((self.root / ".git").exists())
        self.assertTrue(json.loads(result.stdout)["git"])
        tracked = subprocess.run(
            ["git", "-C", str(self.root), "ls-files"],
            text=True, encoding="utf-8", stdout=subprocess.PIPE, check=True,
        )
        self.assertEqual(tracked.stdout, "")
        remote = subprocess.run(
            ["git", "-C", str(self.root), "remote", "get-url", "origin"],
            text=True, encoding="utf-8", stdout=subprocess.PIPE, check=True,
        )
        self.assertEqual(remote.stdout.strip(), "https://example.invalid/not-contacted.git")
        self.check()

    def test_python_310_syntax_and_no_shell_scripts(self):
        for path in SKILL.rglob("*"):
            if path.suffix == ".py":
                ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))
            self.assertNotIn(path.suffix.lower(), {".sh", ".bash", ".bat", ".ps1"})


if __name__ == "__main__":
    unittest.main()
