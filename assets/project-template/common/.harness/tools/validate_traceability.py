#!/usr/bin/env python3
"""只读校验单一追溯事实源与原始证据，不运行项目测试。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from harnesslib import read_json, safe_path, validate_traceability


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出机器可读检查结果")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        data = read_json(safe_path(root, ".harness/traceability.json", ".harness"))
        errors = validate_traceability(root, data)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        errors = [f"无法读取追溯数据：{exc}"]
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "project_tests_executed": False}, ensure_ascii=False))
    else:
        for error in errors:
            print(f"失败：{error}", file=sys.stderr)
        print("追溯校验" + ("失败" if errors else "通过") + "；未运行项目测试。")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
