#!/usr/bin/env python3
"""检查或同步由追溯数据生成的状态投影；不改验证记录和交接。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from harnesslib import (STATE_END, STATE_START, read_json, render_current_block,
                        render_dashboard, render_deliverables, render_tasks, safe_path, validate_traceability)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="明确更新三个完整投影及 current.md 自动块")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        data = read_json(safe_path(root, ".harness/traceability.json", ".harness"))
        project = read_json(safe_path(root, ".harness/project.json", ".harness"))
        errors = validate_traceability(root, data)
        if not isinstance(project, dict):
            errors.append("project.json 必须为对象")
        if errors:
            raise ValueError("\n".join(errors))
        directory = root / ".harness/progress"
        expected = {
            "dashboard.md": render_dashboard(data, project),
            "tasks.md": render_tasks(data),
            "deliverables.md": render_deliverables(data),
        }
        current = safe_path(root, ".harness/progress/current.md", ".harness/progress")
        text = current.read_text(encoding="utf-8")
        if text.count(STATE_START) != 1 or text.count(STATE_END) != 1:
            raise ValueError("current.md 必须恰有一对自动状态标记，拒绝覆盖人工恢复信息")
        start, end = text.index(STATE_START), text.index(STATE_END) + len(STATE_END)
        if start >= end:
            raise ValueError("current.md 自动状态标记顺序错误")
        expected["current.md"] = text[:start] + render_current_block(data) + text[end:]
        if len(expected["dashboard.md"].splitlines()) > 50 or len(expected["dashboard.md"]) > 4000:
            raise ValueError("看板超出 50 行或 4000 字符限制")
        drifted = []
        for name, content in expected.items():
            path = safe_path(root, f".harness/progress/{name}", ".harness/progress", exists=False)
            if path.is_symlink():
                raise ValueError(f"拒绝写入软链接投影：{name}")
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                drifted.append(name)
        if args.write:
            for name in drifted:
                (directory / name).write_text(expected[name], encoding="utf-8", newline="\n")
            print("已同步：" + ("、".join(drifted) or "无变化") + "；交接和原始验证记录未改动。")
            return 0
        if drifted:
            raise ValueError("状态投影漂移：" + "、".join(drifted) + "；审核事实源后使用 --write 同步")
        print("状态投影一致；未写入任何文件。")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError, RecursionError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
