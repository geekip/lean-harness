"""使用真实初始化项目测试信息架构检查器；所有写入均在临时目录。"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]
INITIALIZER = SKILL_ROOT / "scripts/init_project.py"


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lean-harness-checker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        result = subprocess.run(
            [
                sys.executable, "-B", str(INITIALIZER), "--target", str(self.root),
                "--project-type", "code", "--requirements-mode", "qa",
                "--git-mode", "skip", "--json",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read_json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write_json(self, relative: str, data: object) -> None:
        self.write(relative, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def check(self, *arguments: str, success: bool = True) -> dict:
        result = subprocess.run(
            [sys.executable, "-B", str(self.root / ".harness/tools/check.py"), "--json", *arguments],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertNotIn("Traceback", result.stderr, result.stderr)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIs(payload["ok"], success, payload)
        self.assertIs(payload["project_tests_run"], False)
        return payload

    def assert_problem(self, fragment: str, *arguments: str) -> dict:
        result = self.check(*arguments, success=False)
        self.assertTrue(any(fragment in error for error in result["errors"]), result["errors"])
        return result

    def test_pristine_project_and_unchecked_release_are_explicit(self) -> None:
        result = self.check()
        self.assertTrue(any("未检查发布包" in note for note in result["notes"]))
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertFalse((self.root / ".harness/tools/__pycache__").exists())

    def test_relative_reference_unicode_anchor_and_parenthesized_links(self) -> None:
        self.write(".harness/design/有 空格(1).md", "# 配置 `入口`\n\n## 相同\n## 相同\n")
        self.write(
            ".harness/design/links.md",
            '[目标](<有 空格(1).md#配置-入口> "标题")\n'
            "[引用][ref]\n[ref]: <有 空格(1).md#相同-1>\n"
            "![图片](有%20空格%281%29.md)\n"
            "`[示例](absent.md)`\n```md\n[示例](missing.md)\n```\n",
        )
        self.check()

    def test_bad_local_link_anchor_and_reference_are_rejected(self) -> None:
        self.write(".harness/design/links.md", "[目标](README.md#不存在)\n[引用][missing]\n[越界](../../../../secret)\n")
        result = self.assert_problem("Markdown 锚点不存在")
        self.assertTrue(any("引用未定义" in error for error in result["errors"]))
        self.assertTrue(any("越出项目" in error for error in result["errors"]))

    def test_absolute_file_links_bad_urls_and_fences_fail_without_traceback(self) -> None:
        self.write(
            ".harness/design/links.md",
            "[盘符](C:/secret.md)\n[文件](file:///etc/passwd)\n[坏URL](http://[bad)\n```\n",
        )
        result = self.assert_problem("必须使用项目内相对路径")
        self.assertTrue(any("链接格式无效" in error for error in result["errors"]))
        self.assertTrue(any("围栏未闭合" in error for error in result["errors"]))

    def test_root_agents_links_are_active(self) -> None:
        with (self.root / "AGENTS.md").open("a", encoding="utf-8") as stream:
            stream.write("\n[不存在的入口](.harness/missing.md)\n")
        self.assert_problem("Markdown 链接不存在")

    def test_original_sources_are_not_reinterpreted_as_active_markdown(self) -> None:
        self.write(
            ".harness/document/sources/imported/note.md",
            "# 用户原稿\n[来源内的旧链接](not-available.md)\n```\n",
        )
        self.check()

    def test_git_mode_malformed_types_report_errors_in_json(self) -> None:
        path = ".harness/project.json"
        project = self.read_json(path)
        for value in ({}, [], None, 0, True):
            with self.subTest(value=value):
                project["boundaries"]["git"] = value
                self.write_json(path, project)
                self.assert_problem("boundaries.git 仅支持")

    def test_malformed_boundaries_and_trace_do_not_traceback(self) -> None:
        path = ".harness/project.json"
        project = self.read_json(path)
        for value in ([], None, "invalid"):
            with self.subTest(boundaries=value):
                project["boundaries"] = value
                self.write_json(path, project)
                self.assert_problem("project.boundaries 必须是对象")
        trace = self.read_json(".harness/traceability.json")
        trace["state"] = []
        self.write_json(".harness/traceability.json", trace)
        self.check(success=False)

    def test_dashboard_drift_and_readability_limits(self) -> None:
        self.write(".harness/progress/dashboard.md", "已完成 100%，所有能力均已通过。\n" * 51)
        result = self.assert_problem("状态投影漂移")
        self.assertTrue(any("50 行" in error for error in result["errors"]))

    def test_tasks_deliverables_and_current_cannot_drift(self) -> None:
        for name in ("tasks.md", "deliverables.md", "current.md"):
            with self.subTest(projection=name):
                path = self.root / ".harness/progress" / name
                original = path.read_text(encoding="utf-8")
                if name == "current.md":
                    changed = original.replace("<!-- harness:state:start -->", "<!-- stale -->")
                else:
                    changed = original + "\n额外的非事实源任务。\n"
                path.write_text(changed, encoding="utf-8")
                self.check(success=False)
                path.write_text(original, encoding="utf-8")
        self.check()

    def test_directory_responsibilities_and_duplicate_fact_sources(self) -> None:
        self.write(".harness/document/engineering.md", "# 技术规范误放\n")
        self.write(".harness/design/traceability.json", "{}")
        result = self.assert_problem("document 混入非用户资料")
        self.assertTrue(any("重复活动追溯事实源" in error for error in result["errors"]))

    def test_handoff_tampering_and_unregistered_handoff(self) -> None:
        trace = self.read_json(".harness/traceability.json")
        self.write(trace["state"]["latest_handoff"], "# 被覆盖的交接\n")
        self.write(".harness/progress/handoffs/unregistered.md", "# 未登记交接\n")
        result = self.assert_problem("不可覆盖交接的哈希不匹配")
        self.assertTrue(any("交接未登记" in error for error in result["errors"]))

    def test_historical_links_are_exempt_but_snapshot_hash_is_checked(self) -> None:
        self.write(".harness/history/migration-1/README.md", "historical: true\n\n非当前事实源\n")
        snapshot = self.write(".harness/history/migration-1/old.md", "[已失效的旧路径](gone.md)\n")
        self.write(".harness/design/new.md", "# 当前技术说明\n")
        self.write_json(
            ".harness/history/migration-1/migration.json",
            {
                "schema_version": 1, "historical": True, "created_at": "2026-01-01",
                "moves": [{
                    "old": "docs/old.md", "new": ".harness/design/new.md",
                    "snapshot": ".harness/history/migration-1/old.md",
                    "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                }],
            },
        )
        self.check()
        snapshot.write_text("被覆盖的历史", encoding="utf-8")
        self.assert_problem("历史快照哈希不匹配")

    def test_public_readme_source_and_binary_residual(self) -> None:
        self.write("README.md", "# 产品\n\n## 内部评估\n当前内部实验。\n")
        self.write("src/main.c", 'const char *state = ".harness/progress/current.md";\n')
        self.write("program.bin", "")
        (self.root / "program.bin").write_bytes(b"\x00\x01debug .harness/validation/report.md")
        result = self.assert_problem("公开文件残留")
        self.assertTrue(any("README.md" in error for error in result["errors"]))
        self.assertTrue(any("main.c" in error for error in result["errors"]))
        self.assertTrue(any("program.bin" in error for error in result["errors"]))

    def test_safe_release_scan_is_distinct_from_no_release_check(self) -> None:
        self.write("dist/user-guide.txt", "Product quick start\n")
        with zipfile.ZipFile(self.root / "dist/product.zip", "w") as archive:
            archive.writestr("manual.md", "# Product user guide\n")
        result = self.check("--release", "dist")
        self.assertFalse(any("未检查发布包" in note for note in result["notes"]))
        self.assertTrue(any("发布内容扫描" in note for note in result["notes"]))

    def test_release_zip_and_tar_reject_internal_directories_and_content(self) -> None:
        with zipfile.ZipFile(self.root / "release.zip", "w") as archive:
            archive.writestr("package/.harness/", b"")
            archive.writestr("manual.md", b"See .harness/document")
        with tarfile.open(self.root / "release.tar", "w") as archive:
            entry = tarfile.TarInfo("package/.harness/secret.md")
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"log"))
        result = self.assert_problem("发布档案公开残留", "--release", "release.zip", "--release", "release.tar")
        self.assertTrue(any("release.zip!" in error for error in result["errors"]))
        self.assertTrue(any("release.tar!" in error for error in result["errors"]))

    def test_release_traversal_nested_and_damaged_archives_are_not_silently_skipped(self) -> None:
        with zipfile.ZipFile(self.root / "release.zip", "w") as archive:
            archive.writestr("../escape.txt", b"not extracted")
            archive.writestr("nested.zip", b"PK\x03\x04")
        self.write("broken.tar", "not a tar archive")
        result = self.assert_problem("发布档案成员路径越界", "--release", "release.zip", "--release", "broken.tar")
        self.assertTrue(any("嵌套压缩包" in error for error in result["errors"]))
        self.assertTrue(any("格式损坏或不支持" in error for error in result["errors"]))

    def test_symlink_escape_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="lean-harness-outside-") as outside:
            secret = Path(outside) / "private.md"
            secret.write_text("外部资料", encoding="utf-8")
            try:
                (self.root / "leak.md").symlink_to(secret)
            except (NotImplementedError, OSError):
                self.skipTest("当前平台/权限无法创建软链接")
            self.assert_problem("符号链接越出项目")

    @unittest.skipUnless(shutil.which("git"), "Git 未安装")
    def test_user_managed_git_vs_explicit_excluded(self) -> None:
        result = subprocess.run(["git", "init", str(self.root)], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.check()
        project = self.read_json(".harness/project.json")
        project["boundaries"]["git"] = "excluded"
        self.write_json(".harness/project.json", project)
        self.assert_problem("显式 excluded 边界存在未忽略文件")
        self.write(".gitignore", ".harness/\nAGENTS.md\n")
        self.check()
        result = subprocess.run(
            ["git", "-C", str(self.root), "add", "-f", "AGENTS.md"],
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_problem("显式 excluded 边界存在已跟踪文件")


if __name__ == "__main__":
    unittest.main()
