"""Harness 的单一追溯事实源、不可覆盖验证记录与确定性状态投影。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from urllib.parse import quote


DONE = {"verified", "complete"}
STATE_START = "<!-- harness:state:start -->"
STATE_END = "<!-- harness:state:end -->"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
DOMAINS = ("document", "design", "plans", "progress", "validation", "history", "memory", "skills", "tools")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON 不允许非有限数字：{value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 对象包含重复字段：{key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                      object_pairs_hook=unique_object)


def safe_path(root: Path, value: object, prefix: str | None = None, exists: bool = True) -> Path:
    """只接受规范化项目相对文件路径；不得通过软链接逃出工程。"""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"无效项目相对路径：{value!r}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"路径不得绝对化或含空段、.、..：{value!r}")
    if ":" in relative.parts[0]:
        raise ValueError(f"路径不得包含驱动器：{value!r}")
    if prefix and not value.startswith(prefix.rstrip("/") + "/"):
        raise ValueError(f"路径必须位于 {prefix}/：{value}")
    path = root / value
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"路径越出项目：{value}")
    if prefix and not resolved.is_relative_to((root / prefix).resolve()):
        raise ValueError(f"路径通过软链接越出所属目录：{value}")
    if exists and not path.is_file():
        raise ValueError(f"文件不存在：{value}")
    return path


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def _timestamp(value: object) -> bool:
    if not _text(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _index(data: dict, name: str, errors: list[str], key: str = "id") -> dict[str, dict]:
    items = data.get(name)
    if not isinstance(items, list):
        errors.append(f"{name} 必须为数组")
        return {}
    result = {}
    for number, item in enumerate(items):
        if not isinstance(item, dict) or not _text(item.get(key)):
            errors.append(f"{name}[{number}] 必须为对象且包含非空 {key}")
            continue
        identifier = item[key]
        if identifier in result:
            errors.append(f"{name} 重复 {key}：{identifier}")
        result[identifier] = item
    return result


def _refs(item: dict, name: str, available: dict, label: str, errors: list[str]) -> list[str]:
    values = item.get(name)
    if not _strings(values):
        errors.append(f"{label}.{name} 必须为非空字符串组成的数组")
        return []
    if len(set(values)) != len(values):
        errors.append(f"{label}.{name} 有重复引用")
    for value in values:
        if value not in available:
            errors.append(f"{label}.{name} 引用不存在：{value}")
    return values


def _state(item: dict, choices: set[str], label: str, errors: list[str]) -> None:
    value = item.get("status")
    if not isinstance(value, str) or value not in choices:
        errors.append(f"{label}.status 无效：{value!r}")


def _path(root: Path, value: object, label: str, errors: list[str], prefix: str | None = None,
          exists: bool = True) -> Path | None:
    try:
        return safe_path(root, value, prefix, exists)
    except (ValueError, OSError, RuntimeError) as exc:
        errors.append(f"{label}：{exc}")
        return None


def scope_digest(root: Path, data: dict, task: dict) -> str:
    """覆盖验收条件及其原文；任务推进状态不使证据失效。"""
    scope: dict[str, Any] = {
        "task": {key: task.get(key) for key in
                 ("id", "title", "kind", "requirements", "decisions", "designs", "plan", "tests", "artifacts")},
    }
    for collection, key in (("requirements", "source"), ("decisions", "source"),
                            ("designs", "path"), ("plans", "path")):
        selected = [task.get("plan")] if collection == "plans" else task.get(collection, [])
        entries = []
        for item in data.get(collection, []):
            if item["id"] in selected:
                entries.append({"entry": item, "sha256": sha256(safe_path(root, item[key]))})
        scope[collection] = entries
    encoded = json.dumps(scope, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_record(root: Path, record: object, task_id: str, run_id: str) -> list[str]:
    errors: list[str] = []
    label = f"验证 {task_id}/{run_id}"
    if not isinstance(record, dict):
        return [f"{label} 记录必须为对象"]
    if record.get("task") != task_id or record.get("run_id") != run_id:
        errors.append(f"{label} 身份与账本不一致")
    _state(record, {"pass", "fail", "interrupted"}, label, errors)
    if not _timestamp(record.get("recorded_at")):
        errors.append(f"{label} 缺少有效 recorded_at")
    if not isinstance(record.get("scope_sha256"), str) or not HASH.fullmatch(record["scope_sha256"]):
        errors.append(f"{label} 缺少有效验收条件摘要 scope_sha256")
    candidate = record.get("candidate")
    if not isinstance(candidate, dict) or not _text(candidate.get("identity")):
        errors.append(f"{label} 缺少候选身份 candidate.identity")
        candidate = {}
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(f"{label} candidate.artifacts 必须为数组")
        artifacts = []
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append(f"{label} 候选文件必须为对象")
            continue
        path = artifact.get("path")
        _path(root, path, label, errors, exists=False)
        if isinstance(path, str):
            if path in seen:
                errors.append(f"{label} 候选文件重复：{path}")
            seen.add(path)
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            errors.append(f"{label} 候选哈希无效：{path}")
    if not isinstance(record.get("environment"), dict) or not record["environment"]:
        errors.append(f"{label} environment 必须为非空对象")
    commands = record.get("commands")
    if not isinstance(commands, list) or not commands:
        errors.append(f"{label} commands 必须为非空数组")
        commands = []
    for command in commands:
        if not isinstance(command, dict) or not _strings(command.get("argv")) or not command["argv"]:
            errors.append(f"{label} 命令 argv 必须为非空字符串数组")
            continue
        if "exit_code" not in command:
            errors.append(f"{label} 命令缺少 exit_code（未退出时显式使用 null）")
        code = command.get("exit_code")
        if type(code) is not int and code is not None:
            errors.append(f"{label} exit_code 必须为整数或 null")
        if record.get("status") == "pass" and (type(code) is not int or code != 0):
            errors.append(f"{label} pass 记录包含未成功退出的命令")
    if not isinstance(record.get("metrics"), dict):
        errors.append(f"{label} metrics 必须为对象（无指标时使用空对象）")
    if not _strings(record.get("boundaries")) or not record["boundaries"]:
        errors.append(f"{label} boundaries 必须说明非空适用边界")
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{label} 必须保留原始证据，失败/中断也不能省略")
        evidence = []
    prefix = f".harness/validation/{task_id}/runs/{run_id}/evidence"
    seen = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            errors.append(f"{label} evidence 项必须为对象")
            continue
        path = _path(root, entry.get("path"), label, errors, prefix)
        value = entry.get("path")
        if isinstance(value, str):
            if value in seen:
                errors.append(f"{label} 原始证据路径重复：{value}")
            seen.add(value)
        if path and sha256(path) != entry.get("sha256"):
            errors.append(f"{label} 原始证据哈希不一致：{entry.get('path')}")
    return errors


def _validate_traceability(root: Path, data: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["追溯数据必须为 JSON 对象"]
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        errors.append("traceability.schema_version 必须为 2")
    if not _text(data.get("project")):
        errors.append("traceability.project 必须为非空字符串")
    names = ("requirements", "decisions", "designs", "plans", "tasks", "tests", "capabilities", "versions")
    indices = {name: _index(data, name, errors) for name in names}
    requirements, decisions, designs, plans, tasks, tests, capabilities, versions = (
        indices[name] for name in names)
    artifacts = _index(data, "artifacts", errors, "path")
    for identifier, item in requirements.items():
        _state(item, {"draft", "frozen", "superseded"}, identifier, errors)
        _path(root, item.get("source"), identifier, errors, ".harness/document")
        criteria = item.get("acceptance_criteria")
        if not _strings(criteria) or (item.get("status") == "frozen" and not criteria):
            errors.append(f"{identifier} 缺少有效 acceptance_criteria")
    for identifier, item in decisions.items():
        _state(item, {"draft", "confirmed", "superseded"}, identifier, errors)
        _path(root, item.get("source"), identifier, errors, ".harness/document")
    for identifier, item in designs.items():
        _state(item, {"draft", "approved", "superseded"}, identifier, errors)
        _path(root, item.get("path"), identifier, errors, ".harness/design")
        reqs = _refs(item, "requirements", requirements, identifier, errors)
        decs = _refs(item, "decisions", decisions, identifier, errors)
        if not reqs and not decs:
            errors.append(f"{identifier} 设计必须追溯用户需求或决策")
    for identifier, item in plans.items():
        _state(item, {"frozen", "superseded"}, identifier, errors)
        _path(root, item.get("path"), identifier, errors, ".harness/plans")
        if not _refs(item, "designs", designs, identifier, errors):
            errors.append(f"{identifier} 冻结方案必须关联技术设计")
        if not _strings(item.get("acceptance_criteria")) or not item["acceptance_criteria"]:
            errors.append(f"{identifier} 方案缺少验收标准")
    for path, artifact in artifacts.items():
        _state(artifact, {"planned", "implemented", "verified"}, path, errors)
        _path(root, path, path, errors, exists=artifact.get("status") != "planned")
    task_refs = {}
    for identifier, task in tasks.items():
        if not IDENTIFIER.fullmatch(identifier):
            errors.append(f"任务号必须为安全路径标识：{identifier}")
        if not _text(task.get("title")):
            errors.append(f"{identifier} 缺少 title")
        if task.get("kind") not in ("development", "governance"):
            errors.append(f"{identifier} kind 必须为 development 或 governance")
        _state(task, {"planned", "in_progress", "blocked", "verified", "complete"}, identifier, errors)
        refs = {name: _refs(task, name, available, identifier, errors) for name, available in (
            ("requirements", requirements), ("decisions", decisions), ("designs", designs),
            ("tests", tests), ("artifacts", artifacts), ("depends", tasks))}
        task_refs[identifier] = refs
        for field in ("plan", "report"):
            if field not in task:
                errors.append(f"{identifier} 缺少 {field}（尚无关联时显式使用 null）")
        plan_id = task.get("plan")
        if plan_id is not None and (not isinstance(plan_id, str) or plan_id not in plans):
            errors.append(f"{identifier} 引用未知实施方案")
        if task.get("kind") == "development":
            if not refs["requirements"] and not refs["decisions"]:
                errors.append(f"{identifier} 开发工作缺少用户需求或决策")
            for req_id in refs["requirements"]:
                if requirements.get(req_id, {}).get("status") != "frozen":
                    errors.append(f"{identifier} 开发工作引用未冻结需求：{req_id}")
            for dec_id in refs["decisions"]:
                if decisions.get(dec_id, {}).get("status") != "confirmed":
                    errors.append(f"{identifier} 开发工作引用未确认决策：{dec_id}")
            if not refs["designs"]:
                errors.append(f"{identifier} 开发工作缺少技术设计")
            covered_reqs, covered_decs = set(), set()
            for design_id in refs["designs"]:
                design = designs.get(design_id, {})
                if design.get("status") != "approved":
                    errors.append(f"{identifier} 开发工作引用未批准设计：{design_id}")
                if _strings(design.get("requirements")):
                    covered_reqs.update(design["requirements"])
                if _strings(design.get("decisions")):
                    covered_decs.update(design["decisions"])
            if not set(refs["requirements"]).issubset(covered_reqs) or not set(refs["decisions"]).issubset(covered_decs):
                errors.append(f"{identifier} 任务用户依据未被所引用设计覆盖")
            plan = plans.get(plan_id, {}) if isinstance(plan_id, str) else {}
            if plan.get("status") != "frozen":
                errors.append(f"{identifier} 开发工作缺少已冻结实施方案")
            if not _strings(plan.get("designs")) or not set(refs["designs"]).issubset(plan["designs"]):
                errors.append(f"{identifier} 实施方案未覆盖任务设计")
        for test_id in refs["tests"]:
            if tests.get(test_id, {}).get("task") != identifier:
                errors.append(f"{identifier} 测试不属于此工作包：{test_id}")
        report = task.get("report")
        expected = f".harness/validation/{identifier}/report.md"
        if report is not None and report != expected:
            errors.append(f"{identifier} 结论报告必须固定为 {expected}")
        if report is not None:
            _path(root, report, identifier, errors, f".harness/validation/{identifier}")
    remaining = {key: set(refs["depends"]) & tasks.keys() for key, refs in task_refs.items()}
    while remaining:
        ready = {key for key, dependencies in remaining.items() if not dependencies}
        if not ready:
            errors.append("任务依赖存在循环：" + "、".join(sorted(remaining)))
            break
        remaining = {key: dependencies - ready for key, dependencies in remaining.items() if key not in ready}
    run_entries = data.get("validation_runs")
    if not isinstance(run_entries, list):
        errors.append("validation_runs 必须为数组")
        run_entries = []
    runs: dict[tuple[str, str], dict] = {}
    latest = {}
    for entry in run_entries:
        if not isinstance(entry, dict):
            errors.append("validation_runs 项必须为对象")
            continue
        task_id, run_id = entry.get("task"), entry.get("run_id")
        if not isinstance(task_id, str) or task_id not in tasks:
            errors.append("验证记录引用未知任务")
            continue
        if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
            errors.append(f"{task_id} run_id 非安全标识")
            continue
        key = task_id, run_id
        if key in runs:
            errors.append(f"验证轮次重复：{task_id}/{run_id}")
        expected = f".harness/validation/{task_id}/runs/{run_id}/record.json"
        if entry.get("record") != expected:
            errors.append(f"验证轮次记录必须位于 {expected}")
            continue
        path = _path(root, expected, run_id, errors, f".harness/validation/{task_id}/runs/{run_id}")
        if not path:
            continue
        if sha256(path) != entry.get("sha256"):
            errors.append(f"验证轮次账本哈希不一致：{task_id}/{run_id}")
        try:
            record = read_json(path)
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(f"验证轮次 JSON 无法读取：{task_id}/{run_id}：{exc}")
            continue
        record_errors = validate_record(root, record, task_id, run_id)
        errors.extend(record_errors)
        if isinstance(record, dict):
            runs[key] = record
            latest[task_id] = run_id
            if tasks[task_id].get("report") != f".harness/validation/{task_id}/report.md":
                errors.append(f"{task_id} 已有验证记录但未关联固定报告")
    validation_root = root / ".harness/validation"
    if validation_root.is_dir():
        registered = {entry.get("record") for entry in run_entries
                      if isinstance(entry, dict) and isinstance(entry.get("record"), str)}
        for path in validation_root.glob("*/runs/*/record.json"):
            relative = path.relative_to(root).as_posix()
            if relative not in registered:
                errors.append(f"存在未登记验证轮次：{relative}")
        for directory in validation_root.glob("*/runs/*"):
            if directory.is_dir() and not (directory / "record.json").is_file():
                errors.append(f"存在不完整验证轮次（必须保留审计）：{directory.relative_to(root).as_posix()}")
    for test_id, test in tests.items():
        _state(test, {"not_run", "pass", "fail", "interrupted"}, test_id, errors)
        task_id, run_id = test.get("task"), test.get("run")
        if "run" not in test:
            errors.append(f"{test_id} 缺少 run（未运行时显式使用 null）")
        if not isinstance(task_id, str) or task_id not in tasks:
            errors.append(f"{test_id} 引用未知任务")
            continue
        if test_id not in task_refs[task_id]["tests"]:
            errors.append(f"{test_id} 未被所属任务反向关联")
        if test.get("status") == "not_run":
            if run_id is not None:
                errors.append(f"{test_id} not_run 不得关联结果轮次")
        elif not isinstance(run_id, str) or (task_id, run_id) not in runs:
            errors.append(f"{test_id} 缺少所属任务的验证轮次")
        elif test.get("status") != runs[task_id, run_id].get("status"):
            errors.append(f"{test_id} 状态与验证记录不一致")
    for task_id, run_id in latest.items():
        task = tasks[task_id]
        report = root / f".harness/validation/{task_id}/report.md"
        if report.is_file() and report.read_text(encoding="utf-8") != render_report(root, data, task_id):
            errors.append(f"{task_id} 报告与追加验证记录不一致；不得删去失败/中断轮次")
        if task.get("status") not in DONE:
            continue
        record = runs[task_id, run_id]
        if record.get("status") != "pass":
            errors.append(f"{task_id} 已完成但最后轮次不是 pass")
        refs = task_refs[task_id]
        if not refs["tests"]:
            errors.append(f"{task_id} 已完成但没有验收测试")
        for test_id in refs["tests"]:
            test = tests.get(test_id, {})
            if test.get("status") != "pass" or test.get("run") != run_id:
                errors.append(f"{task_id} 测试必须绑定最后通过轮次：{test_id}")
        for dependency in refs["depends"]:
            if tasks.get(dependency, {}).get("status") not in DONE:
                errors.append(f"{task_id} 已完成但依赖未完成：{dependency}")
        candidate = record.get("candidate", {})
        candidate_artifacts = candidate.get("artifacts", []) if isinstance(candidate, dict) else []
        candidate_map = {item["path"]: item.get("sha256") for item in candidate_artifacts
                         if isinstance(item, dict) and isinstance(item.get("path"), str)} if isinstance(candidate_artifacts, list) else {}
        if set(refs["artifacts"]) != set(candidate_map):
            errors.append(f"{task_id} 候选文件清单与任务交付物不一致")
        if task.get("kind") == "development" and not refs["artifacts"]:
            errors.append(f"{task_id} 开发任务已完成但没有实现交付物")
        for artifact in refs["artifacts"]:
            path = _path(root, artifact, task_id, errors)
            if path and sha256(path) != candidate_map.get(artifact):
                errors.append(f"{task_id} 当前实现与通过候选哈希不一致：{artifact}")
            if artifacts.get(artifact, {}).get("status") != "verified":
                errors.append(f"{task_id} 已完成但交付物未验证：{artifact}")
        try:
            if scope_digest(root, data, task) != record.get("scope_sha256"):
                errors.append(f"{task_id} 当前需求/设计/验收条件与验证时不一致")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{task_id} 无法核验验收条件：{exc}")
    for task_id, task in tasks.items():
        if task.get("status") in DONE and task_id not in latest:
            errors.append(f"{task_id} 已完成但没有原始验证记录")
    for path, artifact in artifacts.items():
        if artifact.get("status") == "verified" and not any(
                path in task_refs[task_id]["artifacts"] and task.get("status") in DONE
                for task_id, task in tasks.items()):
            errors.append(f"{path} 标记 verified 但没有已验收任务支撑")
    for identifier, capability in capabilities.items():
        _state(capability, {"verified", "unimplemented"}, identifier, errors)
        if not _text(capability.get("summary")):
            errors.append(f"{identifier} 能力摘要不能为空")
        references = _refs(capability, "tasks", tasks, identifier, errors)
        if capability.get("status") == "verified":
            if not references:
                errors.append(f"{identifier} 已验证能力缺少任务依据")
            for task_id in references:
                task = tasks.get(task_id, {})
                if task.get("kind") != "development" or task.get("status") not in DONE:
                    errors.append(f"{identifier} 已验证能力只能引用已验收开发任务：{task_id}")
    for identifier, version in versions.items():
        if not _text(version.get("title")):
            errors.append(f"{identifier} 版本缺少 title")
        if not _refs(version, "tasks", tasks, identifier, errors):
            errors.append(f"{identifier} 版本计划必须关联真实任务")
    handoffs = data.get("handoffs")
    if not isinstance(handoffs, list):
        errors.append("handoffs 必须为数组")
        handoffs = []
    paths = set()
    for handoff in handoffs:
        if not isinstance(handoff, dict):
            errors.append("handoffs 项必须为对象")
            continue
        path = _path(root, handoff.get("path"), "交接记录", errors, ".harness/progress/handoffs")
        value = handoff.get("path")
        if isinstance(value, str):
            if value in paths:
                errors.append(f"交接路径重复：{value}")
            paths.add(value)
        if path and sha256(path) != handoff.get("sha256"):
            errors.append(f"交接记录哈希不一致（不得覆盖旧交接）：{handoff.get('path')}")
        if not _timestamp(handoff.get("created_at")):
            errors.append("交接记录 created_at 无效")
    handoff_root = root / ".harness/progress/handoffs"
    if handoff_root.is_dir():
        for path in handoff_root.glob("*.md"):
            if path.name != "README.md" and path.relative_to(root).as_posix() not in paths:
                errors.append(f"未登记交接记录：{path.relative_to(root).as_posix()}")
    state = data.get("state")
    if not isinstance(state, dict):
        errors.append("state 必须为对象")
        state = {}
    for field in ("phase", "action"):
        if not _text(state.get(field)):
            errors.append(f"state.{field} 必须为非空字符串")
    if type(state.get("scope_confirmed")) is not bool:
        errors.append("state.scope_confirmed 必须为布尔值")
    if not _strings(state.get("risks")):
        errors.append("state.risks 必须为字符串数组")
    if not _timestamp(state.get("updated_at")):
        errors.append("state.updated_at 必须为 ISO 日期/时间")
    for pointer in ("next_task", "current_work_package"):
        if pointer not in state:
            errors.append(f"state 缺少 {pointer}（尚无任务时显式使用 null）")
        value = state.get(pointer)
        if value is not None and (not isinstance(value, str) or value not in tasks):
            errors.append(f"state.{pointer} 引用未知任务")
        elif isinstance(value, str) and tasks[value].get("status") in DONE:
            errors.append(f"state.{pointer} 不得指向已完成任务")
    expected_latest = handoffs[-1].get("path") if handoffs and isinstance(handoffs[-1], dict) else None
    if "latest_handoff" not in state:
        errors.append("state 缺少 latest_handoff（尚无交接时显式使用 null）")
    if state.get("latest_handoff") != expected_latest:
        errors.append("state.latest_handoff 必须指向最后追加的交接")
    return errors


def validate_traceability(root: Path, data: dict) -> list[str]:
    """异常输入给出可读错误；不执行代码或测试，不产生 traceback。"""
    try:
        return _validate_traceability(root.resolve(), data)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, RecursionError) as exc:
        return [f"追溯数据结构或文件无法校验：{type(exc).__name__}：{exc}"]


def _cell(value: object, limit: int = 100) -> str:
    text = str(value) if value is not None else "—"
    text = " ".join(text.replace("|", "／").replace("<", "＜").replace(">", "＞").split())
    text = re.sub(r"[\[\]`]", "", text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _link(path: str, label: str, relative_to: str = ".harness/progress") -> str:
    import posixpath
    target = posixpath.relpath(path, relative_to)
    return f"[{_cell(label)}]({quote(target, safe='/.-_')})"


def _summary(items: list[dict], scope: bool = True) -> str:
    total = len(items)
    completed = sum(item.get("status") in DONE for item in items)
    if not total:
        return "尚未登记任务，暂不计算百分比"
    if not scope:
        return f"{completed}/{total} 项已验收；范围未确认，暂不计算百分比"
    return f"{completed}/{total} 项已验收（{completed * 100 // total}%）"


def render_dashboard(data: dict, project: dict) -> str:
    """一到两屏，仅状态摘要；完整列表通过入口访问。"""
    state = data["state"]
    tasks = data["tasks"]
    verified = [item for item in data["capabilities"] if item["status"] == "verified"]
    unimplemented = [item for item in data["capabilities"] if item["status"] == "unimplemented"]
    versions = data["versions"]
    lines = [
        f"# {_cell(project.get('name', data['project']), 60)} · 项目看板", "",
        "> 自动状态投影；修改 traceability.json 后运行 sync_views.py --write。", "",
        "| 项目 | 当前事实 |", "| --- | --- |",
        f"| 概述 | {_cell(project.get('summary', '待澄清'), 130)} |",
        f"| 创建 / 更新 | {_cell(project.get('created_at') or '未知')} / {_cell(state['updated_at'])} |",
        f"| 当前阶段 | {_cell(state['phase'])} |",
        f"| 整体进度 | {_summary(tasks, state['scope_confirmed'])} |",
        f"| 当前动作 | {_cell(state['action'], 140)} |",
        f"| 当前工作包 | {_cell(state['current_work_package'])} |",
        f"| NEXT | {_cell(state['next_task'])} |", "",
        "## 主要风险", "",
    ]
    risks = state["risks"]
    lines.extend(f"- {_cell(item, 130)}" for item in risks[:3])
    if not risks:
        lines.append("- 尚未登记风险；不代表没有风险。")
    elif len(risks) > 3:
        lines.append("- 其余风险见 [当前状态](current.md)。")
    lines.extend(["", "## 能力与边界", ""])
    lines.append("- 已验证：" + ("；".join(_cell(item["summary"], 65) for item in verified[:3]) or "尚无已登记验证能力。"))
    lines.append("- 未实现：" + ("；".join(_cell(item["summary"], 65) for item in unimplemented[:3]) or "尚未登记；不可推断为全部实现。"))
    if len(verified) > 3 or len(unimplemented) > 3:
        lines.append("- 完整能力及其证据见 [交付映射](deliverables.md)。")
    lines.extend(["", "## 版本计划", ""])
    if versions:
        task_map = {task["id"]: task for task in tasks}
        for version in versions[:3]:
            members = [task_map[value] for value in version["tasks"] if value in task_map]
            lines.append(f"- {_cell(version['id'], 30)} {_cell(version['title'], 50)}：{_summary(members)}")
        if len(versions) > 3:
            lines.append("- 其他版本见 [任务清单](tasks.md)。")
    else:
        lines.append("- 尚未确定版本计划，不推算版本进度。")
    lines.extend(["", "## 关键入口", "",
                  "[用户需求](../document/requirements.md) · [技术设计](../design/README.md) · "
                  "[实施方案](../plans/README.md) · [任务](tasks.md) · [验证](../validation/README.md) · "
                  "[恢复工作](current.md)"])
    return "\n".join(lines) + "\n"


def render_tasks(data: dict) -> str:
    lines = ["# 完整任务状态", "", "> 由 ../traceability.json 生成；勿单独修改此投影。", "",
             "| 任务 | 内容 | 类型 | 状态 | 依赖 | 验证报告 |", "| --- | --- | --- | --- | --- | --- |"]
    for task in data["tasks"]:
        report = _link(task["report"], "报告") if task.get("report") else "尚未验证"
        lines.append(f"| {_cell(task['id'])} | {_cell(task['title'], 240)} | {_cell(task['kind'])} | "
                     f"{_cell(task['status'])} | {_cell(', '.join(task['depends']))} | {report} |")
    if not data["tasks"]:
        lines.append("| — | 尚未登记任务 | — | — | — | — |")
    lines.extend(["", "## 版本任务映射", ""])
    for version in data["versions"]:
        lines.append(f"- {_cell(version['id'])} {_cell(version['title'])}：{', '.join(version['tasks'])}")
    if not data["versions"]:
        lines.append("尚无版本计划。")
    return "\n".join(lines) + "\n"


def render_deliverables(data: dict) -> str:
    lines = ["# 交付与能力映射", "", "> 由 ../traceability.json 生成；能力声明必须有验证任务支撑。", "",
             "| 交付物 | 状态 | 关联任务 |", "| --- | --- | --- |"]
    for artifact in data["artifacts"]:
        related = [task["id"] for task in data["tasks"] if artifact["path"] in task["artifacts"]]
        title = (_link(artifact["path"], artifact["path"]) if artifact["status"] != "planned"
                 else _cell(artifact["path"]))
        lines.append(f"| {title} | {artifact['status']} | {_cell(', '.join(related), 400)} |")
    if not data["artifacts"]:
        lines.append("| — | 尚无交付物 | — |")
    lines.extend(["", "| 能力 | 声明 | 状态 | 支撑任务 |", "| --- | --- | --- | --- |"])
    for capability in data["capabilities"]:
        lines.append(f"| {_cell(capability['id'])} | {_cell(capability['summary'], 400)} | "
                     f"{capability['status']} | {_cell(', '.join(capability['tasks']), 400)} |")
    if not data["capabilities"]:
        lines.append("| — | 尚无能力声明 | — | — |")
    return "\n".join(lines) + "\n"


def render_current_block(data: dict) -> str:
    state = data["state"]
    counts = Counter(task["status"] for task in data["tasks"])
    handoff = _link(state["latest_handoff"], "最新交接") if state["latest_handoff"] else "尚无交接"
    lines = [STATE_START, "## 当前事实（自动投影）", "",
             f"- 更新时间：{_cell(state['updated_at'])}",
             f"- 阶段：{_cell(state['phase'])}",
             f"- 当前动作：{_cell(state['action'], 2000)}",
             f"- 当前工作包：{_cell(state['current_work_package'])}",
             f"- NEXT：{_cell(state['next_task'])}",
             f"- 任务计数：{len(data['tasks'])} 总计；" +
             "；".join(f"{status}={counts[status]}" for status in ("planned", "in_progress", "blocked", "verified", "complete")),
             f"- 最新交接：{handoff}", "", "### 当前风险", ""]
    lines.extend(f"- {_cell(risk, 2000)}" for risk in state["risks"])
    if not state["risks"]:
        lines.append("- 尚未登记。")
    lines.extend(["", STATE_END])
    return "\n".join(lines)


def render_report(root: Path, data: dict, task_id: str) -> str:
    entries = [entry for entry in data["validation_runs"] if entry["task"] == task_id]
    records = [read_json(safe_path(root, entry["record"])) for entry in entries]
    failed = sum(record["status"] == "fail" for record in records)
    interrupted = sum(record["status"] == "interrupted" for record in records)
    lines = [f"# {task_id} 验证报告", "",
             "> 由追加式验证记录生成；不得删除或覆盖失败、中断轮次。工具不执行项目测试，也不自动批准验收。", "",
             f"- 工作包：{task_id}（当前标题与任务状态见追溯事实源）",
             f"- 最新验证结果：{records[-1]['status'] if records else '尚未执行'}（不等同于任务验收状态）",
             f"- 累计轮次：{len(records)}；失败：{failed}；中断：{interrupted}", ""]
    for record in records:
        lines.extend([f"## 轮次 {record['run_id']} · {record['status']}", "",
                      f"- 记录时间：{_cell(record['recorded_at'])}",
                      f"- 候选身份：{_cell(record['candidate']['identity'], 1000)}",
                      f"- 验收条件摘要：`{record['scope_sha256']}`", "",
                      "### 候选文件", ""])
        for artifact in record["candidate"]["artifacts"]:
            lines.append(f"- `{_cell(artifact['path'], 1000)}` — SHA-256 `{artifact['sha256']}`")
        lines.extend(["", "### 环境与关键指标", "", "```json",
                      json.dumps({"environment": record["environment"], "metrics": record["metrics"]},
                                 ensure_ascii=False, indent=2).replace("```", "\\u0060\\u0060\\u0060"), "```", "",
                      "### 执行命令与退出状态", "", "```json",
                      json.dumps(record["commands"], ensure_ascii=False, indent=2).replace("```", "\\u0060\\u0060\\u0060"),
                      "```", "", "### 适用边界", ""])
        lines.extend(f"- {_cell(value, 2000)}" for value in record["boundaries"])
        lines.extend(["", "### 原始证据", ""])
        for evidence in record["evidence"]:
            lines.append(f"- {_link(evidence['path'], evidence['path'], f'.harness/validation/{task_id}')} "
                         f"— SHA-256 `{evidence['sha256']}`")
        lines.append("")
    return "\n".join(lines)
