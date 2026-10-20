#!/usr/bin/env python3
"""复制既有证据并追加验证轮次；不执行命令，不自动修改任务/测试验收状态。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
from harnesslib import (HASH, IDENTIFIER, read_json, render_report, safe_path, scope_digest,
                        sha256, validate_record, validate_traceability)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run-id", required=True, help="不可重复的轮次号")
    parser.add_argument("--input", required=True, type=Path, help="验证记录 JSON；evidence 使用 source 文件路径")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    created = False
    try:
        if not IDENTIFIER.fullmatch(args.task) or not IDENTIFIER.fullmatch(args.run_id):
            raise ValueError("任务号和轮次号只能含安全英文标识字符，长度 1–80")
        trace_path = safe_path(root, ".harness/traceability.json", ".harness")
        if trace_path.is_symlink():
            raise ValueError("追溯事实源不得为软链接")
        before = trace_path.read_bytes()
        data = read_json(trace_path)
        errors = validate_traceability(root, data)
        if errors:
            raise ValueError("请先修正事实源：\n" + "\n".join(errors))
        task = next((task for task in data["tasks"] if task["id"] == args.task), None)
        if task is None:
            raise ValueError(f"未知工作包：{args.task}")
        if task["status"] in ("verified", "complete"):
            raise ValueError("重验已验收任务前须先将任务退回 in_progress，并同步能力/交付状态；旧证据仍保留")
        record = read_json(args.input)
        if not isinstance(record, dict):
            raise ValueError("输入必须为 JSON 对象")
        prefix = f".harness/validation/{args.task}/runs/{args.run_id}"
        run_directory = safe_path(root, prefix, ".harness/validation", exists=False)
        if run_directory.exists() or run_directory.is_symlink():
            raise ValueError(f"验证轮次已存在，禁止覆盖：{prefix}")
        report_path = safe_path(root, f".harness/validation/{args.task}/report.md",
                                ".harness/validation", exists=False)
        if report_path.is_symlink():
            raise ValueError("报告不得为软链接")
        if report_path.exists() and not any(entry["task"] == args.task for entry in data["validation_runs"]):
            raise ValueError("已存在未登记报告，拒绝覆盖；请先人工核对并归档")
        supplied = record.get("evidence")
        if not isinstance(supplied, list) or not supplied:
            raise ValueError("evidence 必须包含 source；失败或中断也需保存已有日志/中断说明")
        copies = []
        evidence = []
        for number, entry in enumerate(supplied, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                raise ValueError("每个 evidence 必须包含 source 字符串")
            source = Path(entry["source"]).expanduser()
            if not source.is_absolute():
                source = root / source
            if not source.is_file():
                raise ValueError(f"证据文件不存在：{source}")
            destination = f"{prefix}/evidence/{number:03d}-{source.name}"
            safe_path(root, destination, prefix, exists=False)
            copies.append((source, root / destination))
            evidence.append({"path": destination, "sha256": sha256(source)})
        record["task"] = args.task
        record["run_id"] = args.run_id
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        record["scope_sha256"] = scope_digest(root, data, task)
        record["evidence"] = evidence
        candidate = record.get("candidate")
        if not isinstance(candidate, dict) or not isinstance(candidate.get("artifacts"), list):
            raise ValueError("candidate 必须包含 identity 与 artifacts 数组")
        candidate_paths = []
        for item in candidate["artifacts"]:
            if not isinstance(item, dict):
                raise ValueError("candidate.artifacts 必须为对象数组")
            path = safe_path(root, item.get("path"))
            value = item.get("sha256")
            if not isinstance(value, str) or not HASH.fullmatch(value):
                raise ValueError("候选文件必须显式提供 SHA-256，不能自动替用户认证")
            if sha256(path) != value:
                raise ValueError(f"候选文件已变化或哈希不匹配：{item.get('path')}")
            candidate_paths.append(item["path"])
        if set(candidate_paths) != set(task["artifacts"]):
            raise ValueError("候选文件必须精确覆盖任务 artifacts")
        errors = [error for error in validate_record(root, record, args.task, args.run_id)
                  if "文件不存在：" not in error]
        if errors:
            raise ValueError("\n".join(errors))
        json.dumps(record, ensure_ascii=False, allow_nan=False)
        # 任一步失败留下的独立轮次必须人工审计，不静默删除，不复用轮次号。
        run_directory.mkdir(parents=True, exist_ok=False)
        created = True
        (run_directory / "evidence").mkdir()
        for source, destination in copies:
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
        errors = validate_record(root, record, args.task, args.run_id)
        if errors:
            raise ValueError("\n".join(errors))
        record_path = run_directory / "record.json"
        with record_path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(record, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        if trace_path.read_bytes() != before:
            raise ValueError("事实源发生并发修改；保留本轮证据，拒绝覆盖账本，请人工恢复登记")
        data["validation_runs"].append({"task": args.task, "run_id": args.run_id,
                                       "record": record_path.relative_to(root).as_posix(),
                                       "sha256": sha256(record_path)})
        task["report"] = report_path.relative_to(root).as_posix()
        data["state"]["updated_at"] = record["recorded_at"]
        report_path.write_text(render_report(root, data, args.task), encoding="utf-8", newline="\n")
        trace_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                              encoding="utf-8", newline="\n")
        print(f"已追加 {args.task}/{args.run_id}（{record['status']}）；未修改任务或测试状态，未运行项目命令。")
        print("请审核验收条件与证据后更新事实源，再运行 sync_views.py --write。")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError, RecursionError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        if created:
            print("已创建的独立轮次未删除；请保留并审计，不得复用或覆盖该轮次号。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
