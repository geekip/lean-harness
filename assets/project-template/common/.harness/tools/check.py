#!/usr/bin/env python3
"""检查 Harness 信息边界与状态投影；不执行项目测试，也不修改 Git 配置。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit
import zipfile

# 检查不应产生需要 Git 忽略的缓存文件。
sys.dont_write_bytecode = True

from harnesslib import (
    render_current_block,
    render_dashboard,
    render_deliverables,
    render_tasks,
    validate_traceability,
)


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_DIRS = (
    "document", "design", "plans", "progress", "validation",
    "history", "memory", "skills", "tools",
)
REQUIRED_FILES = (
    "AGENTS.md", ".harness/README.md", ".harness/project.json",
    ".harness/traceability.json", ".harness/document/README.md",
    ".harness/document/requirements.md", ".harness/document/requirements-freeze.md",
    ".harness/document/decisions.md", ".harness/document/sources/README.md",
    ".harness/design/README.md", ".harness/design/engineering.md",
    ".harness/plans/README.md", ".harness/validation/README.md",
    ".harness/validation/strategy.md", ".harness/history/README.md",
    ".harness/memory/project.md", ".harness/progress/dashboard.md",
    ".harness/progress/current.md", ".harness/progress/tasks.md",
    ".harness/progress/deliverables.md", ".harness/progress/handoffs/README.md",
    ".harness/tools/check.py", ".harness/tools/harnesslib.py",
    ".harness/tools/validate_traceability.py", ".harness/tools/sync_views.py",
    ".harness/tools/record_validation.py",
)
OLD_DOCUMENT_FILES = {
    "dashboard.md", "engineering.md", "development-plan.md", "testing.md",
    "validation.md", "traceability.json", "current.md", "tasks.md",
    "deliverables.md", "report.md", "strategy.md",
    "architecture.md", "design.md", "api-contract.md", "implementation-plan.md",
    "test-plan.md", "test-report.md",
}
PUBLIC_INTERNAL_NAMES = {
    "development-plan.md", "requirements-freeze.md", "traceability.json",
    "handoff.md", "handoffs.md", "internal-evaluation.md",
}
SKIP_PUBLIC_DIRS = {
    ".harness", ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
INTERNAL_TEXT = re.compile(
    r"(?i)(?:^|[^a-z0-9_])\.harness(?:-runtime)?(?:[/\\]|\b)"
    r"|(?:^|[/\\\s`(])AGENTS\.md\b"
    r"|^\s*#{1,6}\s+.*(?:开发计划|测试过程|交接记录|内部评估"
    r"|development plan|implementation plan|handoff record|internal evaluation|test process|validation report)",
    re.MULTILINE,
)
TOKEN_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
STATE_START = "<!-- harness:state:start -->"
STATE_END = "<!-- harness:state:end -->"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_SCAN_FILE_BYTES = 128 * 1024 * 1024


def label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def confined(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def safe_path(root: Path, raw: object, errors: list[str], context: str) -> Path | None:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        errors.append(f"{context} 必须是使用 / 的项目相对路径")
        return None
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts or re.match(r"^[A-Za-z]:", raw):
        errors.append(f"{context} 路径越界：{raw}")
        return None
    path = root / raw
    if not confined(root, path):
        errors.append(f"{context} 符号链接或路径越出项目：{raw}")
        return None
    return path


def read_text(root: Path, path: Path, errors: list[str]) -> str | None:
    if not confined(root, path):
        errors.append(f"读取路径越出项目：{label(root, path)}")
        return None
    try:
        if not path.is_file():
            errors.append(f"不是可读取的普通文件：{label(root, path)}")
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        errors.append(f"无法读取 {label(root, path)}：{exc}")
        return None


def read_object(root: Path, path: Path, errors: list[str]) -> dict:
    text = read_text(root, path, errors)
    if text is None:
        return {}
    try:
        result = json.loads(text)
    except ValueError as exc:
        errors.append(f"JSON 无效 {label(root, path)}：{exc}")
        return {}
    if not isinstance(result, dict):
        errors.append(f"JSON 顶层必须是对象：{label(root, path)}")
        return {}
    return result


def walk_files(root: Path, start: Path, errors: list[str], public: bool = False) -> list[Path]:
    """不跟随目录软链；项目外的文件/目录软链均报错，不读取目标。"""
    if not confined(root, start):
        errors.append(f"扫描路径越出项目：{label(root, start)}")
        return []
    if start.is_file():
        return [start]
    if not start.is_dir():
        errors.append(f"扫描路径不存在：{label(root, start)}")
        return []
    result = []
    def onerror(exc: OSError) -> None:
        errors.append(f"无法遍历目录：{exc}")
    for directory, dirs, files in os.walk(start, followlinks=False, onerror=onerror):
        parent = Path(directory)
        kept = []
        for name in dirs:
            child = parent / name
            if public and name in SKIP_PUBLIC_DIRS:
                continue
            if not confined(root, child):
                errors.append(f"目录符号链接越出项目：{label(root, child)}")
            elif child.is_symlink():
                errors.append(f"目录软链未扫描，请使用实际目录：{label(root, child)}")
            else:
                kept.append(name)
        dirs[:] = kept
        for name in files:
            child = parent / name
            if not confined(root, child):
                errors.append(f"文件符号链接越出项目：{label(root, child)}")
            elif child.is_file():
                result.append(child)
    return sorted(result)


def strip_code(text: str, inline: bool = True) -> tuple[str, bool]:
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append(line if fence is None else "")
    clean = "\n".join(lines)
    return (re.sub(r"(`+).*?\1", "", clean) if inline else clean), fence is not None


def anchors(text: str) -> set[str]:
    clean, _ = strip_code(text, inline=False)
    found = set(re.findall(r"""(?:id|name)\s*=\s*["']([^"']+)["']""", clean))
    counts: dict[str, int] = {}
    lines = clean.splitlines()
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(match.group(1))
        elif index and re.match(r"^\s{0,3}(?:=+|-+)\s*$", line):
            headings.append(lines[index - 1].strip())
    for heading in headings:
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        heading = re.sub(r"<[^>]+>", "", heading)
        slug = re.sub(r"[^\w\s-]", "", heading.lower())
        slug = re.sub(r"\s", "-", slug)
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        found.add(slug if number == 0 else f"{slug}-{number}")
    return found


def link_destination(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<"):
        end = raw.find(">")
        return raw[1:end] if end >= 0 else raw
    match = re.match(r"""(.*?)(?:\s+["'].*["']|\s+\([^)]*\))?\s*$""", raw)
    return re.sub(r"\\([ ()])", r"\1", match.group(1) if match else raw)


def markdown_links(text: str) -> tuple[list[str], list[str], bool]:
    clean, unclosed = strip_code(text)
    definitions = {}
    for match in re.finditer(r"^\s{0,3}\[([^\]\n]+)\]:\s*(.+)$", clean, re.MULTILINE):
        definitions[" ".join(match.group(1).lower().split())] = link_destination(match.group(2))
    clean = re.sub(r"^\s{0,3}\[[^\]\n]+\]:.*$", "", clean, flags=re.MULTILINE)
    links = []
    unresolved = []
    pattern = re.compile(r"!?\[([^\]\n]*)\]")
    index = 0
    while (match := pattern.search(clean, index)) is not None:
        end = match.end()
        if end < len(clean) and clean[end] == "(":
            depth = 1
            cursor = end + 1
            escaped = False
            while cursor < len(clean) and depth:
                char = clean[cursor]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                cursor += 1
            if depth == 0:
                links.append(link_destination(clean[end + 1:cursor - 1]))
                index = cursor
                continue
        elif end < len(clean) and clean[end] == "[":
            close = clean.find("]", end + 1)
            if close >= 0:
                reference = clean[end + 1:close] or match.group(1)
                key = " ".join(reference.lower().split())
                if key in definitions:
                    links.append(definitions[key])
                elif not key.startswith("^"):
                    unresolved.append(reference)
                index = close + 1
                continue
        else:
            key = " ".join(match.group(1).lower().split())
            if key in definitions:
                links.append(definitions[key])
        index = end
    return links, unresolved, unclosed


def check_markdown(root: Path, path: Path, errors: list[str]) -> None:
    text = read_text(root, path, errors)
    if text is None:
        return
    if TOKEN_RE.search(text):
        errors.append(f"未渲染模板变量：{label(root, path)}")
    links, unresolved, unclosed = markdown_links(text)
    if unclosed:
        errors.append(f"Markdown 代码围栏未闭合：{label(root, path)}")
    for reference in unresolved:
        errors.append(f"Markdown 引用未定义：{label(root, path)} -> [{reference}]")
    for target in links:
        if not target or target.startswith("//"):
            continue
        if re.match(r"^[A-Za-z]:[/\\]", target) or target.lower().startswith("file:"):
            errors.append(f"Markdown 本地链接必须使用项目内相对路径：{label(root, path)} -> {target}")
            continue
        try:
            parsed = urlsplit(target)
        except ValueError:
            errors.append(f"Markdown 链接格式无效：{label(root, path)} -> {target}")
            continue
        if parsed.scheme:
            continue
        raw_path = unquote(parsed.path)
        resolved = path.parent / raw_path if raw_path else path
        if not confined(root, resolved):
            errors.append(f"Markdown 链接越出项目：{label(root, path)} -> {target}")
            continue
        if not resolved.exists():
            errors.append(f"Markdown 链接不存在：{label(root, path)} -> {target}")
            continue
        if parsed.fragment and resolved.suffix.lower() in {".md", ".markdown"}:
            destination = read_text(root, resolved, errors)
            if destination is not None and unquote(parsed.fragment) not in anchors(destination):
                errors.append(f"Markdown 锚点不存在：{label(root, path)} -> {target}")


def exempt_markdown(root: Path, path: Path, data: dict) -> bool:
    parts = path.relative_to(root).parts
    if parts[:2] == (".harness", "history"):
        return parts != (".harness", "history", "README.md")
    if parts[:3] == (".harness", "document", "sources"):
        return parts != (".harness", "document", "sources", "README.md")
    if parts[:3] == (".harness", "progress", "handoffs") and path.name != "README.md":
        state = data.get("state")
        if isinstance(state, dict):
            return path.relative_to(root).as_posix() != state.get("latest_handoff")
    return len(parts) >= 5 and parts[:2] == (".harness", "validation") and parts[3] == "runs"


def check_history(root: Path, errors: list[str]) -> None:
    history = root / ".harness/history"
    overview = read_text(root, history / "README.md", errors)
    if overview is not None and "非当前事实源" not in overview:
        errors.append("history/README.md 必须声明历史资料为“非当前事实源”")
    if not confined(root, history) or not history.is_dir():
        return
    for snapshot in sorted(history.iterdir()):
        if snapshot.name == "README.md":
            continue
        if not confined(root, snapshot) or snapshot.is_symlink() or not snapshot.is_dir():
            errors.append(f"历史条目必须是项目内快照目录：{label(root, snapshot)}")
            continue
        readme = read_text(root, snapshot / "README.md", errors)
        if readme is not None and (
            not re.search(r"\bhistorical:\s*true\b", readme) or "非当前事实源" not in readme
        ):
            errors.append(f"历史快照缺少 historical: true / 非当前事实源声明：{label(root, snapshot)}")
        manifest = read_object(root, snapshot / "migration.json", errors)
        if manifest.get("historical") is not True:
            errors.append(f"历史清单必须 historical=true：{label(root, snapshot)}")
        moves = manifest.get("moves")
        if not isinstance(moves, list) or not moves:
            errors.append(f"历史清单必须记录非空 moves：{label(root, snapshot)}")
            continue
        registered = {snapshot / "README.md", snapshot / "migration.json"}
        for item in moves:
            if not isinstance(item, dict):
                errors.append(f"历史 moves 条目必须是对象：{label(root, snapshot)}")
                continue
            for key in ("old", "new"):
                safe_path(root, item.get(key), errors, f"历史 {key}")
            archived = safe_path(root, item.get("snapshot"), errors, "历史 snapshot")
            if archived is None:
                continue
            try:
                archived.relative_to(snapshot)
            except ValueError:
                errors.append(f"历史 snapshot 不在所属快照目录：{label(root, archived)}")
                continue
            registered.add(archived)
            if not archived.is_file():
                errors.append(f"历史快照文件不存在：{label(root, archived)}")
            else:
                try:
                    if hashlib.sha256(archived.read_bytes()).hexdigest() != item.get("sha256"):
                        errors.append(f"历史快照哈希不匹配：{label(root, archived)}")
                except OSError as exc:
                    errors.append(f"无法读取历史快照 {label(root, archived)}：{exc}")
        for path in walk_files(root, snapshot, errors):
            if path not in registered:
                errors.append(f"历史文件未纳入哈希清单：{label(root, path)}")


def check_projections(root: Path, data: dict, project: dict, errors: list[str]) -> None:
    outputs = (
        ("dashboard.md", lambda: render_dashboard(data, project)),
        ("tasks.md", lambda: render_tasks(data)),
        ("deliverables.md", lambda: render_deliverables(data)),
    )
    for filename, render in outputs:
        path = root / ".harness/progress" / filename
        text = read_text(root, path, errors)
        try:
            expected = render()
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            errors.append(f"无法构建 {filename} 投影，先修复追溯数据：{exc}")
            continue
        if text is not None and text != expected:
            errors.append(f"状态投影漂移：{label(root, path)}；核实事实源后运行 sync_views.py --write")
        if filename == "dashboard.md" and text is not None:
            if len(text.splitlines()) > 50 or len(text) > 4000:
                errors.append("dashboard 超过 50 行或 4000 字符，请压缩事实源摘要，保留关键入口")
    path = root / ".harness/progress/current.md"
    text = read_text(root, path, errors)
    if text is not None:
        if text.count(STATE_START) != 1 or text.count(STATE_END) != 1:
            errors.append("progress/current.md 必须有且仅有一组状态投影标记")
        elif text.index(STATE_START) >= text.index(STATE_END):
            errors.append("progress/current.md 状态投影标记顺序错误")
        else:
            block = text[text.index(STATE_START):text.index(STATE_END) + len(STATE_END)]
            try:
                if block != render_current_block(data).strip():
                    errors.append("progress/current.md 状态/最新交接投影漂移")
            except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
                errors.append(f"无法构建 current 状态投影：{exc}")


def check_latest_handoff(root: Path, data: dict, errors: list[str]) -> None:
    entries = data.get("handoffs")
    state = data.get("state")
    if not isinstance(entries, list) or not entries or not isinstance(state, dict):
        errors.append("追溯数据必须登记初始化交接和当前状态")
        return
    last = entries[-1]
    if not isinstance(last, dict) or state.get("latest_handoff") != last.get("path"):
        errors.append("state.latest_handoff 必须指向追加清单中的最后一份交接")
    registered = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("handoffs 条目必须是对象")
            continue
        path = safe_path(root, entry.get("path"), errors, "交接")
        if path is None:
            continue
        if path.parent != root / ".harness/progress/handoffs" or path.name == "README.md":
            errors.append(f"交接路径必须在 progress/handoffs：{label(root, path)}")
            continue
        if path in registered:
            errors.append(f"交接重复登记：{label(root, path)}")
        registered.add(path)
        try:
            if not path.is_file():
                errors.append(f"交接不是普通文件：{label(root, path)}")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"无法读取交接 {label(root, path)}：{exc}")
            continue
        if actual != entry.get("sha256"):
            errors.append(f"不可覆盖交接的哈希不匹配：{label(root, path)}")
    directory = root / ".harness/progress/handoffs"
    if directory.is_dir():
        for path in walk_files(root, directory, errors):
            if path.name != "README.md" and path not in registered:
                errors.append(f"交接未登记到单一事实源：{label(root, path)}")


def public_violation(name: str, content: bytes) -> str | None:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if any(part.lower() in {".harness", ".harness-runtime", "agents.md"} for part in parts):
        return "包含 Harness 或 AI 内部资料路径"
    if parts and parts[-1].lower() in PUBLIC_INTERNAL_NAMES:
        return "文件名属于内部开发过程资料"
    if re.search(rb"(?i)\.harness(?:-runtime)?[/\\]", content):
        return "包含内部 Harness 路径（包括二进制字符串）"
    if b"\x00" in content[:8192]:
        return None
    text = content.decode("utf-8-sig", errors="replace")
    match = INTERNAL_TEXT.search(text)
    if match:
        return f"出现内部路径或过程标题：{match.group(0).strip()[:100]}"
    return None


def check_archive(root: Path, path: Path, errors: list[str]) -> None:
    """仅在内存中查看档案成员；不解压、不跟随归档中的链接。"""
    archive_label = label(root, path)
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS or sum(i.file_size for i in members) > MAX_ARCHIVE_BYTES:
                    errors.append(f"发布档案超出检查限额，未完整检查：{archive_label}")
                    return
                for item in members:
                    if item.is_dir():
                        inspect_member(archive_label, item.filename, b"", errors)
                        continue
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        errors.append(f"发布档案含软链，无法认证边界：{archive_label}!{item.filename}")
                        continue
                    inspect_member(archive_label, item.filename, archive.read(item), errors)
            return
        if tarfile.is_tarfile(path):
            with tarfile.open(path, mode="r:*") as archive:
                total = 0
                count = 0
                for item in archive:
                    count += 1
                    total += item.size
                    if count > MAX_ARCHIVE_MEMBERS or total > MAX_ARCHIVE_BYTES:
                        errors.append(f"发布档案超出检查限额，未完整检查：{archive_label}")
                        return
                    if item.isdir():
                        inspect_member(archive_label, item.name, b"", errors)
                        continue
                    if not item.isfile():
                        errors.append(f"发布档案含链接或特殊文件：{archive_label}!{item.name}")
                        continue
                    stream = archive.extractfile(item)
                    if stream is not None:
                        inspect_member(archive_label, item.name, stream.read(), errors)
            return
        if path.name.lower().endswith((".zip", ".tar", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2", ".7z", ".rar")):
            errors.append(f"发布档案格式损坏或不支持，未完整检查：{archive_label}")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
        errors.append(f"无法完整检查发布档案 {archive_label}：{exc}")


def inspect_member(archive: str, name: str, content: bytes, errors: list[str]) -> None:
    member = PurePosixPath(name.replace("\\", "/"))
    if member.is_absolute() or ".." in member.parts or re.match(r"^[A-Za-z]:", name):
        errors.append(f"发布档案成员路径越界：{archive}!{name}")
    violation = public_violation(name, content)
    if violation:
        errors.append(f"发布档案公开残留：{archive}!{name}：{violation}")
    if content.startswith((b"PK\x03\x04", b"\x1f\x8b")) or name.lower().endswith((".tar", ".zip", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2")):
        errors.append(f"发布档案含嵌套压缩包，请单独提供展开后的发布目录检查：{archive}!{name}")


def scan_bytes(root: Path, path: Path, errors: list[str]) -> bytes | None:
    try:
        if path.stat().st_size > MAX_SCAN_FILE_BYTES:
            errors.append(f"文件超过静态扫描限额，未完整检查：{label(root, path)}")
            return None
        return path.read_bytes()
    except OSError as exc:
        errors.append(f"无法扫描 {label(root, path)}：{exc}")
        return None


def check_public(root: Path, project: dict, releases: list[str], errors: list[str], notes: list[str]) -> list[Path]:
    boundaries = project.get("boundaries", {})
    if not isinstance(boundaries, dict):
        errors.append("project.boundaries 必须是对象")
        boundaries = {}
    paths = walk_files(root, root, errors, public=True)
    configured_public = boundaries.get("public_paths", [])
    configured_releases = boundaries.get("release_paths", [])
    for key, value in (("public_paths", configured_public), ("release_paths", configured_releases)):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"boundaries.{key} 必须是路径字符串列表")
    if not isinstance(configured_public, list):
        configured_public = []
    if not isinstance(configured_releases, list):
        configured_releases = []
    for raw in configured_public:
        path = safe_path(root, raw, errors, "公开扫描")
        if path is not None:
            paths.extend(walk_files(root, path, errors))
    active_markdown = []
    count = 0
    for path in sorted(set(paths)):
        if path.name.upper() == "AGENTS.MD" or path.name in {".gitignore", ".gitmodules", ".gitattributes", ".editorconfig"}:
            continue
        if ".harness" in path.relative_to(root).parts:
            errors.append(f"公开扫描配置指向 Harness 内部资料：{label(root, path)}")
            continue
        content = scan_bytes(root, path, errors)
        if content is None:
            continue
        count += 1
        violation = public_violation(label(root, path), content)
        if violation:
            errors.append(f"公开文件残留：{label(root, path)}：{violation}")
        if path.suffix.lower() in {".md", ".markdown"}:
            active_markdown.append(path)
    notes.append(f"公开文件静态扫描：{count} 个文件；仅检查路径、文件名和过程标题，不认证自然语言语义完整性")
    raw_releases = list(dict.fromkeys([item for item in configured_releases if isinstance(item, str)] + releases))
    if not raw_releases:
        notes.append("未检查发布包：未配置 boundaries.release_paths，也未提供 --release")
    for raw in raw_releases:
        path = safe_path(root, raw, errors, "发布")
        if path is None:
            continue
        files = walk_files(root, path, errors)
        for item in files:
            content = scan_bytes(root, item, errors)
            if content is None:
                continue
            violation = public_violation(label(root, item), content)
            if violation:
                errors.append(f"发布文件残留：{label(root, item)}：{violation}")
            check_archive(root, item, errors)
        notes.append(f"发布内容扫描：{raw}，{len(files)} 个文件；未执行发布/测试")
    return active_markdown


def check_git(root: Path, project: dict, errors: list[str], notes: list[str]) -> None:
    boundaries = project.get("boundaries", {})
    mode = boundaries.get("git", "user-managed") if isinstance(boundaries, dict) else "user-managed"
    if not isinstance(mode, str) or mode not in {"user-managed", "excluded"}:
        errors.append("boundaries.git 仅支持 user-managed 或 excluded")
        return
    executable = shutil.which("git")
    if executable is None:
        (errors if mode == "excluded" else notes).append("Git 边界未检查：找不到 Git")
        return
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [executable, "-C", str(root), *args], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    try:
        probe = run("rev-parse", "--show-toplevel")
        if probe.returncode:
            (errors if mode == "excluded" else notes).append("Git 边界未检查：项目不在 Git 工作树中")
            return
        tracked = run("ls-files", "--", ".harness", "AGENTS.md")
        if tracked.returncode:
            errors.append(f"Git 跟踪边界读取失败：{tracked.stderr.strip()}")
            return
        candidates = [".harness/traceability.json", ".harness/progress/dashboard.md", "AGENTS.md"]
        candidates.extend(
            label(root, item) for item in walk_files(root, root / ".harness", errors)
        )
        candidates = sorted(set(candidates))
        ignored = []
        not_ignored = []
        for candidate in candidates:
            result = run("check-ignore", "--no-index", "-q", "--", candidate)
            if result.returncode == 0:
                ignored.append(candidate)
            elif result.returncode == 1:
                not_ignored.append(candidate)
            else:
                errors.append(f"Git 忽略查询失败：{candidate}：{result.stderr.strip()}")
        tracked_files = tracked.stdout.splitlines()
        notes.append(
            f"Git 边界 {mode}：已跟踪 {len(tracked_files)} 个内部文件，"
            f"被忽略 {len(ignored)} 个，未忽略 {len(not_ignored)} 个；未修改规则或索引"
        )
        if mode == "excluded":
            if tracked_files:
                errors.append("显式 excluded 边界存在已跟踪文件：" + ", ".join(tracked_files[:8]))
            if not_ignored:
                errors.append("显式 excluded 边界存在未忽略文件：" + ", ".join(not_ignored[:8]))
    except OSError as exc:
        (errors if mode == "excluded" else notes).append(f"Git 边界未检查：{exc}")


def audit(root: Path, releases: list[str]) -> dict:
    errors: list[str] = []
    notes: list[str] = []
    root = root.resolve()
    harness = root / ".harness"
    project = read_object(root, harness / "project.json", errors)
    data = read_object(root, harness / "traceability.json", errors)
    for directory in REQUIRED_DIRS:
        if not (harness / directory).is_dir() or not confined(root, harness / directory):
            errors.append(f"缺少项目内目录：.harness/{directory}")
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file() or not confined(root, root / relative):
            errors.append(f"缺少项目内文件：{relative}")
    if project.get("project_type") == "code":
        for name in (".editorconfig", "README.md"):
            if not (root / name).is_file():
                errors.append(f"代码工程缺少根文件：{name}")
    for obsolete in ("guide", "agents", "workflows", "standards", "specs", "changes", "schemas", "adapters"):
        if (harness / obsolete).exists():
            errors.append(f"活动布局包含职责已迁移的目录：.harness/{obsolete}；先迁移而非复制")
    files = walk_files(root, harness, errors)
    for path in files:
        relative = path.relative_to(harness)
        if relative.parts[0] in {"history", "document"} and (
            relative.parts[0] == "history" or relative.parts[:2] == ("document", "sources")
        ):
            continue
        if path.name == "traceability.json" and path != harness / "traceability.json":
            errors.append(f"存在重复活动追溯事实源：{label(root, path)}")
        if relative.parts[0] == "document" and path.name in OLD_DOCUMENT_FILES:
            errors.append(f"document 混入非用户资料：{label(root, path)}")
    if project.get("schema_version") != 2 or project.get("layout_version") != 2:
        errors.append("project.json 需要 schema_version=2 与 layout_version=2；旧工程应先迁移审查")
    try:
        errors.extend(validate_traceability(root, data))
    except Exception as exc:
        errors.append(f"追溯校验无法完成：{type(exc).__name__}：{exc}")
    check_projections(root, data, project, errors)
    check_latest_handoff(root, data, errors)
    check_history(root, errors)
    public_markdown = check_public(root, project, releases, errors, notes)
    active_markdown = files + public_markdown + [root / "AGENTS.md"]
    for path in sorted(set(active_markdown)):
        if path.suffix.lower() in {".md", ".markdown"} and not exempt_markdown(root, path, data):
            check_markdown(root, path, errors)
    skills = harness / "skills"
    skill_entries = [path for path in files if path.name == "SKILL.md" and skills in path.parents]
    if not skill_entries:
        errors.append(".harness/skills 缺少项目技能入口")
    for path in skill_entries:
        text = read_text(root, path, errors)
        if text is not None:
            frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|$)", text, re.DOTALL)
            if frontmatter is None or not re.search(
                rf"(?m)^name:\s*{re.escape(path.parent.name)}\s*$", frontmatter.group(1)
            ) or not re.search(r"(?m)^description:\s*\S", frontmatter.group(1)):
                errors.append(f"项目技能 frontmatter 无效：{label(root, path)}")
    check_git(root, project, errors, notes)
    notes.extend([
        "Markdown 检查限于活动文档的本地相对路径、引用式链接和标题锚点；不联网验证外部 URL",
        "目录职责检查覆盖已知文件位置和追溯注册路径；任意文档内容仍需人工审查",
        "哈希用于检测记录漂移，不提供外部不可变存储或抵御账本与文件同时篡改的保证",
        "未运行项目测试；结构和投影通过不等于产品功能通过",
    ])
    return {"ok": not errors, "errors": errors, "notes": notes, "project_tests_run": False}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="append", default=[], metavar="PATH", help="项目内发布目录或 zip/tar，可重复")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = parser.parse_args()
    result = audit(ROOT, args.release)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for error in result["errors"]:
            print(f"失败：{error}", file=sys.stderr)
        for note in result["notes"]:
            print(note)
        print("通过：Harness 信息架构检查" if result["ok"] else f"失败：发现 {len(result['errors'])} 个 Harness 问题")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
