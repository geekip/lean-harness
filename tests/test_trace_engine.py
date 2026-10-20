"""追溯、证据追加和状态投影回归；仅使用临时项目与 Python 标准库。"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parents[1] / "assets/project-template/common/.harness/tools"
SPEC = importlib.util.spec_from_file_location("harnesslib_regression", TOOLS / "harnesslib.py")
lib = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lib)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-trace-")
        self.root = Path(self.temp.name)
        self.tools = self.root / ".harness/tools"
        self.tools.mkdir(parents=True)
        for name in ("harnesslib.py", "validate_traceability.py", "sync_views.py", "record_validation.py"):
            shutil.copy2(TOOLS / name, self.tools / name)
        for directory in lib.DOMAINS:
            (self.root / ".harness" / directory).mkdir(exist_ok=True)
        self.data = {
            "schema_version": 2, "project": "example", "requirements": [], "decisions": [],
            "designs": [], "plans": [], "tasks": [], "tests": [], "artifacts": [],
            "capabilities": [], "versions": [], "validation_runs": [], "handoffs": [],
            "state": {"phase": "需求澄清", "action": "澄清需求", "risks": [], "scope_confirmed": False,
                      "updated_at": "2026-10-20", "next_task": None, "current_work_package": None,
                      "latest_handoff": None},
        }
        self.project = {"name": "例子", "summary": "演示", "created_at": None}
        self.write(".harness/project.json", json.dumps(self.project))
        self.write(".harness/progress/current.md", "# 当前\n\n" + lib.render_current_block(self.data) +
                   "\n\n## 恢复资料\n\n不要覆盖人工恢复说明。\n")
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def save(self):
        self.write(".harness/traceability.json", json.dumps(self.data, ensure_ascii=False, indent=2))

    def load(self):
        self.data = json.loads((self.root / ".harness/traceability.json").read_text(encoding="utf-8"))

    def command(self, name, *args, ok=True):
        result = subprocess.run([sys.executable, "-B", str(self.tools / name), *args],
                                cwd=self.root, text=True, encoding="utf-8", capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        return result

    def development(self):
        self.write(".harness/document/requirements.md", "# 需求\n\n输入 name 返回 Hello name。\n")
        self.write(".harness/design/greeting.md", "# 接口\n\nhello(name) -> str。\n")
        self.write(".harness/plans/greeting.md", "# 已冻结方案\n\n实现 hello 并测试确定输出。\n")
        self.write("hello.py", "def hello(name):\n    return 'Hello ' + name\n")
        self.data["requirements"] = [{
            "id": "R1", "source": ".harness/document/requirements.md", "status": "frozen",
            "acceptance_criteria": ["输入 Ada 返回 Hello Ada"]}]
        self.data["designs"] = [{
            "id": "D1", "path": ".harness/design/greeting.md", "status": "approved",
            "requirements": ["R1"], "decisions": []}]
        self.data["plans"] = [{
            "id": "P1", "path": ".harness/plans/greeting.md", "status": "frozen",
            "designs": ["D1"], "acceptance_criteria": ["测试 hello('Ada') == 'Hello Ada'"]}]
        self.data["tasks"] = [{
            "id": "T1", "title": "问候功能", "kind": "development", "status": "in_progress",
            "requirements": ["R1"], "decisions": [], "designs": ["D1"], "plan": "P1",
            "tests": ["TEST1"], "artifacts": ["hello.py"], "depends": [], "report": None}]
        self.data["tests"] = [{"id": "TEST1", "task": "T1", "status": "not_run", "run": None}]
        self.data["artifacts"] = [{"path": "hello.py", "status": "implemented"}]
        self.data["state"].update(scope_confirmed=True, next_task="T1", current_work_package="T1")
        self.save()
        self.assertEqual(lib.validate_traceability(self.root, self.data), [])

    def record(self, run, status="pass", exit_code=0, ok=True):
        self.write("observed.log", "测试夹具中的记录：" + status)
        record = {
            "candidate": {"identity": "working-tree-example",
                          "artifacts": [{"path": "hello.py", "sha256": lib.sha256(self.root / "hello.py")}]},
            "environment": {"python": sys.version.split()[0], "os": sys.platform},
            "commands": [{"argv": ["never-execute-this-test-command", "--fixture"], "exit_code": exit_code}],
            "status": status, "metrics": {"assertions": 1},
            "boundaries": ["这是治理工具回归夹具，不代表真实产品能力已验证。"],
            "evidence": [{"source": "observed.log"}],
        }
        self.write("input.json", json.dumps(record))
        result = self.command("record_validation.py", "--task", "T1", "--run-id", run,
                              "--input", "input.json", ok=ok)
        if ok:
            self.load()
        return result

    def verify(self, run):
        self.data["tasks"][0]["status"] = "verified"
        self.data["tests"][0].update(status="pass", run=run)
        self.data["artifacts"][0]["status"] = "verified"
        self.data["capabilities"] = [{"id": "C1", "summary": "确定性问候输出",
                                     "status": "verified", "tasks": ["T1"]}]
        self.data["state"].update(next_task=None, current_work_package=None)
        self.save()

    def test_empty_and_projection(self):
        self.assertEqual(lib.validate_traceability(self.root, self.data), [])
        self.assertIn("暂不计算百分比", lib.render_dashboard(self.data, self.project))
        self.command("sync_views.py", "--write")
        self.command("sync_views.py")
        self.assertIn("不要覆盖人工恢复说明",
                      (self.root / ".harness/progress/current.md").read_text(encoding="utf-8"))
        self.assertLessEqual(len(lib.render_dashboard(self.data, self.project).splitlines()), 50)
        self.write(".harness/progress/dashboard.md", "虚报 100% 完成")
        self.command("sync_views.py", ok=False)

    def test_confirmed_scope_without_tasks_has_no_percentage(self):
        self.data["state"]["scope_confirmed"] = True
        dashboard = lib.render_dashboard(self.data, self.project)
        self.assertIn("尚未登记任务，暂不计算百分比", dashboard)
        self.assertNotIn("范围未确认", dashboard)
        self.assertNotIn("100%", dashboard)
        self.assertNotIn("0%", dashboard)

    def test_complete_chain_and_tamper(self):
        self.development()
        self.record("run1")
        self.assertEqual(self.data["tasks"][0]["status"], "in_progress")
        self.assertEqual(self.data["tests"][0]["status"], "not_run")
        self.verify("run1")
        self.command("validate_traceability.py")
        self.command("sync_views.py", "--write")
        self.write("hello.py", "changed implementation")
        result = self.command("validate_traceability.py", ok=False)
        self.assertIn("候选哈希不一致", result.stderr)

    def test_failure_interruption_then_pass_preserved(self):
        self.development()
        self.record("failure", "fail", 1)
        old_record = self.root / ".harness/validation/T1/runs/failure/record.json"
        before = old_record.read_bytes()
        self.record("interrupt", "interrupted", None)
        self.record("passed")
        self.verify("passed")
        self.command("validate_traceability.py")
        self.assertEqual(old_record.read_bytes(), before)
        report = (self.root / ".harness/validation/T1/report.md").read_text(encoding="utf-8")
        self.assertIn("失败：1；中断：1", report)
        self.assertIn("轮次 failure · fail", report)
        self.assertEqual(len(self.data["validation_runs"]), 3)

    def test_duplicate_run_and_nonzero_pass(self):
        self.development()
        self.record("existing")
        before = (self.root / ".harness/validation/T1/runs/existing/record.json").read_bytes()
        self.record("existing", ok=False)
        self.assertEqual(before, (self.root / ".harness/validation/T1/runs/existing/record.json").read_bytes())
        self.record("invalid", "pass", 3, ok=False)
        self.assertFalse((self.root / ".harness/validation/T1/runs/invalid").exists())

    def test_acceptance_change_invalidates(self):
        self.development()
        self.record("pass")
        self.verify("pass")
        self.data["plans"][0]["acceptance_criteria"].append("添加尚未验证的新验收条件")
        self.save()
        result = self.command("validate_traceability.py", ok=False)
        self.assertIn("验收条件与验证时不一致", result.stderr)

    def test_task_title_change_can_be_revalidated_after_reopening(self):
        self.development()
        self.record("first")
        self.verify("first")
        old_record = self.root / ".harness/validation/T1/runs/first/record.json"
        old_bytes = old_record.read_bytes()
        self.data["tasks"][0].update(title="修订后的问候功能", status="in_progress")
        self.data["tests"][0].update(status="not_run", run=None)
        self.data["artifacts"][0]["status"] = "implemented"
        self.data["capabilities"][0]["status"] = "unimplemented"
        self.save()
        self.command("validate_traceability.py")
        self.record("revalidation")
        self.verify("revalidation")
        self.command("validate_traceability.py")
        self.assertEqual(old_record.read_bytes(), old_bytes)
        report = (self.root / ".harness/validation/T1/report.md").read_text(encoding="utf-8")
        self.assertIn("工作包：T1", report)
        self.assertIn("轮次 first", report)
        self.assertIn("轮次 revalidation", report)

    def test_malformed_data_no_traceback(self):
        for value in (None, [], {}, {"schema_version": 2, "requirements": None},
                      {"schema_version": 2, "tasks": [{"id": "T1", "status": []}]}):
            self.write(".harness/traceability.json", json.dumps(value))
            self.command("validate_traceability.py", ok=False)
            self.command("sync_views.py", "--write", ok=False)
        for text in ('{"project":1,"project":2}', '{"bad":NaN}', "[" * 1500):
            self.write(".harness/traceability.json", text)
            self.command("validate_traceability.py", ok=False)
        self.development()
        for field in self.data:
            if field == "schema_version":
                continue
            changed = copy.deepcopy(self.data)
            changed[field] = {"unexpected": []}
            self.assertTrue(lib.validate_traceability(self.root, changed), field)

    def test_handoff_append_hash_and_unknown_pointer(self):
        self.write(".harness/progress/handoffs/one.md", "# 不可覆盖的交接\n")
        path = ".harness/progress/handoffs/one.md"
        self.data["handoffs"] = [{"path": path, "sha256": lib.sha256(self.root / path), "created_at": "2026-10-20"}]
        self.data["state"]["latest_handoff"] = path
        self.assertEqual(lib.validate_traceability(self.root, self.data), [])
        self.write(path, "# 被覆盖\n")
        self.assertTrue(any("交接记录哈希" in error for error in lib.validate_traceability(self.root, self.data)))
        self.data["state"]["next_task"] = "UNKNOWN"
        self.assertTrue(any("引用未知任务" in error for error in lib.validate_traceability(self.root, self.data)))

    def test_traversal_cycle_wrong_domain_and_orphan_run(self):
        self.development()
        self.data["designs"][0]["path"] = ".harness/document/requirements.md"
        self.assertTrue(lib.validate_traceability(self.root, self.data))
        self.data["designs"][0]["path"] = ".harness/design/greeting.md"
        self.data["tasks"][0]["depends"] = ["T1"]
        self.assertTrue(any("循环" in error for error in lib.validate_traceability(self.root, self.data)))
        with self.assertRaises(ValueError):
            lib.safe_path(self.root, "../outside")
        self.data["tasks"][0]["depends"] = []
        self.save()
        self.record("orphan")
        self.data["validation_runs"] = []
        self.save()
        self.assertTrue(any("未登记验证轮次" in error for error in lib.validate_traceability(self.root, self.data)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
