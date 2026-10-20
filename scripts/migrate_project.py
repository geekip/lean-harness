#!/usr/bin/env python3
"""显式映射、预演优先的文档迁移；不批准需求、不升级证据、不修改 Git。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit


IMMUTABLE = (".harness/history/", ".harness/progress/handoffs/", ".harness/validation/")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"必须使用项目相对 POSIX 文件路径：{value!r}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")) or ":" in value:
        raise ValueError(f"必须是无空段、.、.. 的规范项目相对路径：{value}")
    path = root / value
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"路径越出项目：{value}")
    if any(part in SKIP_DIRS for part in relative.parts):
        raise ValueError(f"禁止迁移工具/仓库内部路径：{value}")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"不沿软链接迁移：{value}")
        if parent != path and parent.exists() and not parent.is_dir():
            raise ValueError(f"路径父级不是目录：{value}")
    return path


def immutable(value: str) -> bool:
    # 已有验证报告和原始记录也不能用通用迁移覆盖；历史升级需另行审查。
    return value.startswith(IMMUTABLE)


def reserved_destination(value: str) -> bool:
    """新策略可以迁入验证域，轮次记录和累积报告必须由记录流程建立。"""
    return value.startswith((".harness/history/", ".harness/progress/handoffs/")) or (
        value.startswith(".harness/validation/")
        and ("/runs/" in value or value.endswith("/report.md"))
    )


def immutable_markdown(value: str) -> bool:
    """原始记录不改，目录说明和测试策略等活动入口仍需修正链接。"""
    if value in (
        ".harness/history/README.md", ".harness/progress/handoffs/README.md",
        ".harness/document/sources/README.md",
    ):
        return False
    if value.startswith((".harness/history/", ".harness/progress/handoffs/",
                         ".harness/document/sources/")):
        return True
    return value.startswith(".harness/validation/") and (
        "/runs/" in value or value.endswith("/report.md")
    )


def inline_targets(line: str) -> list[tuple[int, int]]:
    """定位 Markdown 行内链接目标；支持平衡括号、转义和尖括号目标。"""
    code_spans = [
        (match.start(), match.end())
        for match in re.finditer(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", line)
    ]
    targets = []
    for match in re.finditer(r"(?<!\\)\]\(", line):
        if any(start <= match.start() < end for start, end in code_spans):
            continue
        start = match.end()
        while start < len(line) and line[start].isspace():
            start += 1
        if start == len(line):
            continue
        if line[start] == "<":
            end = line.find(">", start + 1)
            if end != -1:
                targets.append((start, end + 1))
            continue
        depth, end = 0, start
        while end < len(line):
            char = line[end]
            if char == "\\" and end + 1 < len(line):
                end += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() and depth == 0:
                break
            end += 1
        if end > start and end < len(line) and depth == 0:
            targets.append((start, end))
    return targets


def rewrite_markdown(text: str, old: str, new: str, moves: dict[str, str]) -> str:
    """更新行内/引用式本地链接，并按新位置重定位未迁移的链接。"""
    old_parent, new_parent = posixpath.dirname(old), posixpath.dirname(new)

    def rewrite_target(target: str) -> str:
        bracketed = target.startswith("<") and target.endswith(">")
        raw = target[1:-1] if bracketed else target
        if not raw or raw.startswith(("#", "/")) or re.match(r"^[a-zA-Z][\w+.-]*:", raw):
            return target
        raw = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~\\])", r"\1", raw)
        parts = urlsplit(raw)
        if not parts.path:
            return target
        resolved = posixpath.normpath(posixpath.join(old_parent, unquote(parts.path)))
        destination = moves.get(resolved, resolved)
        rewritten = posixpath.relpath(destination, new_parent or ".")
        if destination == resolved and old_parent == new_parent:
            return target
        rewritten = urlunsplit(("", "", quote(rewritten, safe="/-._~()"), parts.query, parts.fragment))
        if bracketed:
            return "<" + rewritten + ">"
        return rewritten

    # 代码围栏不是链接；仅在围栏外更新 Markdown。
    result = []
    fence = None
    reference = re.compile(r"^(\s{0,3}\[[^\]]+\]:\s*)(<[^>]+>|\S+)(.*)$")
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = None
            result.append(line)
            continue
        if fence is None:
            for start, end in reversed(inline_targets(line)):
                line = line[:start] + rewrite_target(line[start:end]) + line[end:]
            line = reference.sub(lambda m: m[1] + rewrite_target(m[2]) + m[3], line)
        result.append(line)
    return "".join(result)


def rewrite_values(value: object, moves: dict[str, str]) -> object:
    if isinstance(value, str):
        return moves.get(value, value)
    if isinstance(value, list):
        return [rewrite_values(item, moves) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_values(item, moves) for key, item in value.items()}
    return value


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mapping", required=True, help='JSON: {"moves":[{"old":...,"new":...}]}')
    parser.add_argument("--id", help="唯一迁移编号；仅字母、数字、点、下划线、连字符")
    parser.add_argument("--apply", action="store_true", help="显式执行；默认仅预演")
    args = parser.parse_args()
    try:
        root = Path(args.target).expanduser().resolve()
        if root.parent == root or not (root / ".harness").is_dir():
            raise ValueError("目标必须是已有 Harness 项目的根目录")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        migration_id = args.id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", migration_id):
            raise ValueError("迁移编号无效")
        history_rel = f".harness/history/{migration_id}"
        history = safe_path(root, history_rel)
        if history.exists():
            raise ValueError("迁移编号已存在，禁止覆盖历史")
        mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        entries = mapping.get("moves") if isinstance(mapping, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("moves 必须是非空数组")
        moves: dict[str, str] = {}
        originals: dict[str, bytes] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("每项映射必须是对象")
            old, new = entry.get("old"), entry.get("new")
            source, destination = safe_path(root, old), safe_path(root, new)
            if old == new or old in moves or new in moves.values():
                raise ValueError("映射重复或原地覆盖")
            if immutable(old) or reserved_destination(new):
                raise ValueError("不能移动既有验证、交接或历史，或直接生成验证报告/轮次；请另行审查")
            if old == ".harness/project.json" or new == ".harness/project.json":
                raise ValueError("项目元数据不能作为文档迁移")
            if not source.is_file() or destination.exists():
                raise ValueError(f"来源必须存在且目标必须不存在：{old} -> {new}")
            moves[old] = new
            originals[old] = source.read_bytes()

        # 对整组操作一次预检，目标不得落入另一个来源/目标文件内部。
        paths = list(moves) + list(moves.values())
        for left in paths:
            for right in paths:
                if left != right and right.startswith(left.rstrip("/") + "/"):
                    raise ValueError("文件映射路径互相包含")

        edits: dict[str, bytes] = {}
        review_text_references = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in (".md", ".markdown"):
                continue
            relative = path.relative_to(root).as_posix()
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if immutable_markdown(relative):
                continue
            safe_path(root, relative)
            raw = path.read_bytes()
            if relative not in moves:
                originals.setdefault(relative, raw)
            text = raw.decode("utf-8")
            destination = moves.get(relative, relative)
            rewritten_text = rewrite_markdown(text, relative, destination, moves)
            # 非链接路径可能属于用户原话或历史叙述，不能做盲目字符串替换。
            for old in moves:
                mentions = (old, old[9:]) if old.startswith(".harness/") else (old,)
                if any(value in rewritten_text for value in mentions):
                    review_text_references.append({"path": destination, "old_path": old})
            rewritten = rewritten_text.encode("utf-8")
            if rewritten != raw or relative in moves:
                edits[relative] = rewritten
        for old in moves:
            edits.setdefault(old, originals[old])

        trace_old = next(
            (old for old, new in moves.items() if new == ".harness/traceability.json"),
            ".harness/traceability.json",
        )
        trace_path = safe_path(root, trace_old)
        trace = None
        if trace_path.is_file():
            trace_bytes = trace_path.read_bytes()
            trace = json.loads(trace_bytes.decode("utf-8"))
            if not isinstance(trace, dict):
                raise ValueError("追溯账本必须是 JSON 对象")
            updated = rewrite_values(trace, moves)
            # 旧原始证据/交接不可改；账本内对应不可变对象的字段保持原状。
            for field in ("validation_runs", "handoffs"):
                if field in trace:
                    updated[field] = trace[field]
            if updated != trace:
                originals[trace_old] = trace_bytes
                edits[trace_old] = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode()
            trace = updated

        handoff_path = f".harness/progress/handoffs/{migration_id}-migration.md"
        handoff = None
        if trace and trace.get("schema_version") == 2:
            if safe_path(root, handoff_path).exists():
                raise ValueError("迁移交接已存在")
            if not isinstance(trace.get("handoffs"), list) or not isinstance(trace.get("state"), dict):
                raise ValueError("v2 追溯账本缺少 handoffs/state")
            handoff = (
                f"# 文档迁移交接\n\n时间：{timestamp}\n\n"
                f"路径与原文哈希见 [迁移清单](../../history/{migration_id}/migration.json)。\n\n"
                "本次只迁移资料；未改变任务完成结论，也未认证旧证据。"
                "请审查追溯链、更新状态投影并执行结构与证据检查。\n"
            ).encode("utf-8")
            originals.setdefault(trace_old, trace_path.read_bytes())
            trace["handoffs"].append({
                "path": handoff_path, "sha256": digest(handoff), "created_at": timestamp,
            })
            trace["state"]["latest_handoff"] = handoff_path
            trace["state"]["updated_at"] = timestamp
            edits[trace_old] = (json.dumps(trace, ensure_ascii=False, indent=2) + "\n").encode()

        records = [{
            "old": old, "new": moves.get(old, old), "sha256": digest(originals[old]),
            "snapshot": f"{history_rel}/snapshots/{old}",
            "operation": "move" if old in moves else "rewrite",
        } for old in sorted(edits)]
        result = {
            "status": "执行" if args.apply else "预演",
            "target": str(root), "migration_id": migration_id, "changes": records,
            "review_text_references": review_text_references,
            "note": "旧布局/schema 不会自动升级；迁移不代表需求、实现或证据已通过审查",
        }
        if not args.apply:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        # 防止预检期间并发编辑；所有哈希一致后才进行第一次写入。
        for old in edits:
            if safe_path(root, old).read_bytes() != originals[old]:
                raise ValueError(f"来源发生并发变化，停止：{old}")
        history.mkdir(parents=True, exist_ok=False)
        (history / "README.md").write_text(
            "---\nhistorical: true\n---\n\n# 文档迁移快照\n\n"
            "本目录仅用于追溯，是非当前事实源；旧链接和旧术语反映创建时状态。\n"
            "原文以 migration.json 中的 SHA-256 校验，不能覆盖或当作活动文档使用。\n",
            encoding="utf-8", newline="\n",
        )
        for item in records:
            snapshot = safe_path(root, item["snapshot"])
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            with snapshot.open("xb") as stream:
                stream.write(originals[item["old"]])
        (history / "migration.json").write_text(json.dumps({
            "schema_version": 1, "historical": True, "created_at": timestamp, "moves": records,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        for old, content in edits.items():
            source = safe_path(root, old)
            destination = safe_path(root, moves.get(old, old))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if old in moves:
                with destination.open("xb") as stream:
                    stream.write(content)
                source.unlink()
            else:
                source.write_bytes(content)
        if handoff is not None:
            path = safe_path(root, handoff_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(handoff)
        result["status"] = "路径迁移完成；待审查数据并同步投影"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"迁移失败：{exc}；如已写入，原文可从对应 history 快照恢复。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
