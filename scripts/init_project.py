#!/usr/bin/env python3
"""安全、可重复地初始化通用 Lean Harness 工程。"""

from __future__ import annotations

import argparse
from datetime import date
import filecmp
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable


sys.dont_write_bytecode = True
SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = SKILL_ROOT / "assets/project-template"
TOKEN_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
STRUCTURE = {
    "AGENTS.md": "AI 协作入口与按需阅读顺序",
    ".harness/README.md": "信息职责与阅读入口",
    ".harness/project.json": "项目类型和需求来源等初始化记录",
    ".harness/traceability.json": "需求、设计、方案、任务、验证和交付的唯一关联事实源",
    ".harness/document": "用户需求、用户决策、讨论与原始输入",
    ".harness/design": "技术架构、格式规范、接口契约",
    ".harness/plans": "已冻结的实施与验收方案",
    ".harness/validation": "测试策略、独立工作包报告与不可覆盖的原始证据",
    ".harness/history": "明确标记非当前事实源的历史快照与迁移哈希",
    ".harness/memory": "已确认的项目事实、约束和偏好",
    ".harness/progress": "用户看板、续接状态、任务、交付映射和不可覆盖的交接",
    ".harness/skills": "按需加载的项目级方法",
    ".harness/tools": "治理结构与追溯关系自检",
}


class InitError(RuntimeError):
    """表示预检或安全边界失败。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化通用 Lean Harness 工程")
    parser.add_argument("--target", required=True, help="项目根目录")
    parser.add_argument("--name", help="项目显示名称；默认使用目录名")
    parser.add_argument("--project-id", help="稳定项目标识；默认根据名称生成")
    parser.add_argument(
        "--project-type",
        required=True,
        choices=("code", "non-code"),
        help="代码工程或非代码工程",
    )
    parser.add_argument(
        "--create-gitignore",
        action="store_true",
        help="仅代码工程：按用户选择创建缺失的 .gitignore，不修改已有文件",
    )
    parser.add_argument(
        "--requirements-mode",
        required=True,
        choices=("qa", "existing", "defer"),
        help="问答澄清、评审现有资料或暂缓需求澄清",
    )
    parser.add_argument(
        "--requirements-source",
        action="append",
        default=[],
        help="现有需求文件，可重复传入",
    )
    parser.add_argument(
        "--copy-requirements",
        action="store_true",
        help="将需求来源复制到 .harness/document/sources/",
    )
    parser.add_argument(
        "--git-mode",
        required=True,
        choices=("skip", "init"),
        help="跳过 Git 初始化或执行本地 git init",
    )
    parser.add_argument("--remote-url", help="可选 Git 远端地址；只配置，不联网")
    parser.add_argument("--remote-name", default="origin", help="远端名称，默认 origin")
    parser.add_argument(
        "--replace-remote",
        action="store_true",
        help="显式允许替换同名但地址不同的远端",
    )
    parser.add_argument(
        "--allow-nested-git",
        action="store_true",
        help="显式允许在上级仓库中创建嵌套仓库",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="显式替换已有的模板管理文件",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不写入")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser.parse_args()


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """直接运行参数列表并以 UTF-8 捕获输出，不依赖 Shell。"""
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def configure_output() -> None:
    """让终端和重定向输出的中文在各平台均使用 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def normalize_id(value: str) -> str:
    """将名称转换为稳定、长度受限的连字符标识。"""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        normalized = f"project-{digest}"
    return normalized[:40].rstrip("-")


def existing_parent(path: Path) -> Path:
    """返回最近的现有父目录。"""
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def git_root_for(path: Path) -> Path | None:
    """返回 path 所属 Git 根；不在仓库中时返回 None。"""
    base = path if path.exists() and path.is_dir() else existing_parent(path)
    result = run(["git", "-C", str(base), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def read_existing_project(target: Path) -> dict | None:
    """读取已有初始化元数据。"""
    path = target / ".harness/project.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise InitError(f"已有 .harness/project.json 无法读取：{exc}") from exc
    if not isinstance(value, dict):
        raise InitError("已有 .harness/project.json 不是 JSON 对象")
    return value


def resolve_sources(raw_sources: Iterable[str]) -> list[Path]:
    """解析并校验需求来源。"""
    sources: list[Path] = []
    for raw in raw_sources:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise InitError(f"需求来源不是现有文件：{path}")
        if path not in sources:
            sources.append(path)
    return sources


def source_destinations(target: Path, sources: list[Path]) -> list[Path]:
    """计算导入目标并拒绝同名来源冲突。"""
    destinations: list[Path] = []
    seen: dict[str, Path] = {}
    for source in sources:
        if source.name in seen and seen[source.name] != source:
            raise InitError(
                f"两个需求来源具有相同文件名 {source.name}：{seen[source.name]} 与 {source}"
            )
        seen[source.name] = source
        destinations.append(target / ".harness/document/sources" / source.name)
    return destinations


def check_copy_conflicts(
    sources: list[Path], destinations: list[Path], overwrite: bool
) -> None:
    """在写入前拒绝覆盖内容不同的同名需求来源。"""
    for source, destination in zip(sources, destinations):
        if source == destination.resolve() or not destination.exists() or overwrite:
            continue
        if not filecmp.cmp(source, destination, shallow=False):
            raise InitError(
                f"需求导入目标已存在且内容不同：{destination}；"
                "请改为原地引用、重命名来源，或明确使用 --overwrite-existing"
            )


def requirement_text(mode: str) -> dict[str, str]:
    """返回需求模式对应的中文状态文案。"""
    if mode == "qa":
        return {
            "status": "澄清中（尚未冻结）",
            "freeze": "问答待开始",
            "next": "通过引导问答澄清目标、边界和验收标准",
            "opening": "按问答方式逐项确认目标用户、范围、默认行为、失败边界和验收标准。",
            "freeze_opening": "从目标用户、核心结果和明确排除范围开始第一轮问答。",
        }
    if mode == "existing":
        return {
            "status": "现有资料待评审（尚未冻结）",
            "freeze": "资料评审待开始",
            "next": "评审现有需求资料并通过问答补齐歧义",
            "opening": "核对现有资料的冲突、缺口、默认值、排除项和可验证验收标准。",
            "freeze_opening": "先确认资料优先级，再对冲突和缺失决策逐项提问。",
        }
    return {
        "status": "需求澄清未开始",
        "freeze": "暂缓",
        "next": "确认需求来源以及是否启动问答冻结",
        "opening": "尚未选择需求澄清方式；开始实施前应先确认范围和验收边界。",
        "freeze_opening": "等待用户决定何时开始需求问答。",
    }


def template_files(project_type: str) -> list[tuple[Path, Path]]:
    """返回源模板与模板内相对路径。"""
    result: list[tuple[Path, Path]] = []
    for group in ("common", project_type):
        base = TEMPLATE_ROOT / group
        for source in sorted(
            path
            for path in base.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        ):
            result.append((source, source.relative_to(base)))
    return result


def output_relative(relative: Path, tokens: dict[str, str]) -> Path:
    """将模板中的通用技能目录和初始化交接文件改为项目专用路径。"""
    value = relative.as_posix()
    value = value.replace(
        ".harness/skills/project-harness",
        f".harness/skills/{tokens['__HARNESS_SKILL_NAME__']}",
    )
    value = value.replace(
        ".harness/skills/project-engineering",
        f".harness/skills/{tokens['__WORK_SKILL_NAME__']}",
    )
    value = value.replace(
        ".harness/skills/project-work",
        f".harness/skills/{tokens['__WORK_SKILL_NAME__']}",
    )
    if value.endswith(".harness/progress/handoffs/initialization.md"):
        value = value.rsplit("/", 1)[0] + "/" + tokens["__INITIAL_HANDOFF__"]
    return Path(value)


def render(source: Path, tokens: dict[str, str]) -> str:
    """渲染 UTF-8 文本模板并拒绝遗留变量。"""
    text = source.read_text(encoding="utf-8")
    for token, value in tokens.items():
        text = text.replace(token, value)
    match = TOKEN_RE.search(text)
    if match:
        raise InitError(f"模板 {source} 存在未提供变量：{match.group(0)}")
    return text


def write_text(path: Path, content: str, overwrite: bool, result: dict) -> None:
    """创建或显式覆盖文本文件。"""
    relative = str(path)
    if path.exists() and not overwrite:
        result["preserved"].append(relative)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(content, encoding="utf-8", newline="\n")
    result["overwritten" if existed else "created"].append(relative)


def load_template_library():
    """只导入技能自带的可信渲染器，不加载目标项目的 Python 代码。"""
    path = TEMPLATE_ROOT / "common/.harness/tools/harnesslib.py"
    spec = importlib.util.spec_from_file_location("_harness_template_library", path)
    if spec is None or spec.loader is None:
        raise InitError("无法加载模板渲染器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def protected_record(relative: Path) -> bool:
    """重新初始化不重置追溯账本、交接、历史或验证证据。"""
    value = relative.as_posix()
    return value == ".harness/traceability.json" or value.startswith((
        ".harness/progress/handoffs/", ".harness/validation/", ".harness/history/",
    ))


def preflight_git(args: argparse.Namespace, target: Path) -> Path | None:
    """在写入前校验 Git 初始化、嵌套仓库和远端冲突。"""
    needs_git = args.git_mode == "init" or bool(args.remote_url)
    if needs_git and shutil.which("git") is None:
        raise InitError("需要 Git 操作，但系统中找不到 git")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.remote_name):
        raise InitError(f"远端名称无效：{args.remote_name!r}")

    root = git_root_for(target) if shutil.which("git") else None
    target_has_repository = (target / ".git").exists()
    if args.git_mode == "init" and root and root != target and not target_has_repository:
        if not args.allow_nested_git:
            raise InitError(
                f"目标位于上级 Git 仓库 {root} 中；如确需嵌套仓库，请明确使用 --allow-nested-git"
            )

    if args.remote_url:
        if args.git_mode != "init" and (not root or root != target):
            raise InitError("配置远端需要目标本身已有 Git 仓库，或同时选择 --git-mode init")
        if root == target:
            current = run(["git", "-C", str(target), "remote", "get-url", args.remote_name])
            if current.returncode == 0:
                old_url = current.stdout.strip()
                if old_url != args.remote_url and not args.replace_remote:
                    raise InitError(
                        f"远端 {args.remote_name} 已指向 {old_url}；替换需明确使用 --replace-remote"
                    )
    return root


def configure_git(args: argparse.Namespace, target: Path, result: dict) -> None:
    """按已确认选项初始化 Git 并配置远端，不执行网络操作。"""
    if args.git_mode == "init" and not (target / ".git").exists():
        initialized = run(["git", "init", str(target)])
        if initialized.returncode != 0:
            raise InitError("git init 失败：" + initialized.stderr.strip())
        result["git"].append("已初始化本地 Git 仓库")
    elif args.git_mode == "init":
        result["git"].append("目标已有 Git 仓库，未重复初始化")
    else:
        result["git"].append("按用户选择跳过 Git 初始化")

    if not args.remote_url:
        return
    current = run(["git", "-C", str(target), "remote", "get-url", args.remote_name])
    if current.returncode != 0:
        changed = run(
            ["git", "-C", str(target), "remote", "add", args.remote_name, args.remote_url]
        )
        action = "新增"
    elif current.stdout.strip() == args.remote_url:
        result["git"].append(f"远端 {args.remote_name} 已是指定地址")
        return
    else:
        changed = run(
            ["git", "-C", str(target), "remote", "set-url", args.remote_name, args.remote_url]
        )
        action = "替换"
    if changed.returncode != 0:
        raise InitError(f"配置远端失败：{changed.stderr.strip()}")
    result["git"].append(
        f"已{action}远端 {args.remote_name}；未执行 fetch、pull、push 或其他网络操作"
    )


def validate_generated(
    target: Path, trusted_checks: dict[str, bool]
) -> list[dict[str, object]]:
    """只运行本次生成或内容与模板一致的本地治理检查。"""
    checks: list[dict[str, object]] = []
    for relative in (
        ".harness/tools/check.py",
        ".harness/tools/validate_traceability.py",
    ):
        if not trusted_checks.get(relative, False) or not all(trusted_checks.values()):
            checks.append(
                {
                    "command": f"python {relative}",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "skipped": True,
                    "reason": "保留了内容不同的现有工具或依赖；审阅后再手动运行",
                }
            )
            continue
        process = run([sys.executable, str(target / relative)], cwd=target)
        checks.append(
            {
                "command": f"python {relative}",
                "exit_code": process.returncode,
                "stdout": process.stdout.strip(),
                "stderr": process.stderr.strip(),
            }
        )
    return checks


def print_human(result: dict) -> None:
    """打印简体中文的人读结果。"""
    print(f"Harness 初始化目标：{result['target']}")
    print(f"状态：{result['status']}")
    print(
        f"创建 {len(result['created'])} 个文件，保留 {len(result['preserved'])} 个文件，"
        f"覆盖 {len(result['overwritten'])} 个文件"
    )
    for message in result["git"]:
        print(f"Git：{message}")
    print("\n项目结构与作用：")
    print(Path(result["target"]).name + "/")
    root_entries = [
        (path, purpose)
        for path, purpose in result["structure"].items()
        if not path.startswith(".harness/")
    ]
    for path, purpose in root_entries:
        print(f"├── {path} — {purpose}")
    print("└── .harness/")
    harness_entries = [
        (path.removeprefix(".harness/"), purpose)
        for path, purpose in result["structure"].items()
        if path.startswith(".harness/")
    ]
    for index, (path, purpose) in enumerate(harness_entries):
        branch = "└──" if index == len(harness_entries) - 1 else "├──"
        suffix = "" if Path(path).suffix else "/"
        print(f"    {branch} {path}{suffix} — {purpose}")
    for check in result.get("checks", []):
        if check.get("skipped"):
            print(f"\n{check['command']} -> 已跳过：{check['reason']}")
            continue
        print(f"\n{check['command']} -> {check['exit_code']}")
        if check["stdout"]:
            print(check["stdout"])
        if check["stderr"]:
            print(check["stderr"], file=sys.stderr)


def main() -> int:
    configure_output()
    args = parse_args()
    try:
        target = Path(args.target).expanduser().resolve()
        if target.parent == target:
            raise InitError("拒绝将文件系统根目录作为项目目标")
        if target.exists() and not target.is_dir():
            raise InitError(f"目标不是目录：{target}")
        if args.create_gitignore and args.project_type != "code":
            raise InitError("非代码工程不创建 .gitignore；请移除 --create-gitignore")

        project_name = args.name.strip() if args.name else target.name
        if not project_name:
            raise InitError("项目名称不能为空")
        project_id = normalize_id(args.project_id or project_name)
        if args.project_id and args.project_id != project_id:
            raise InitError(
                "--project-id 只能使用小写字母、数字和单连字符，且最长 40 个字符"
            )

        sources = resolve_sources(args.requirements_source)
        if args.requirements_mode == "existing" and not sources:
            raise InitError("requirements-mode=existing 至少需要一个 --requirements-source")
        if args.copy_requirements and not sources:
            raise InitError("--copy-requirements 需要至少一个 --requirements-source")
        destinations = source_destinations(target, sources) if args.copy_requirements else []
        check_copy_conflicts(sources, destinations, args.overwrite_existing)

        preflight_git(args, target)
        existing = read_existing_project(target)
        legacy_paths = (
            ".harness/guide", ".harness/document/traceability.json",
            ".harness/document/dashboard.md", ".harness/document/engineering.md",
            ".harness/document/development-plan.md", ".harness/document/testing.md",
            ".harness/document/validation.md",
        )
        if any((target / value).exists() for value in legacy_paths) or (
            existing and existing.get("layout_version") != 2
        ):
            raise InitError(
                "发现旧信息架构；停止初始化以避免多个事实源。"
                "请先按 references/migration.md 审查路径映射、归档和追溯数据，不能用覆盖参数跳过迁移"
            )
        existing_trace = None
        trace_path = target / ".harness/traceability.json"
        if trace_path.exists():
            existing_trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if not isinstance(existing_trace, dict) or existing_trace.get("schema_version") != 2:
                raise InitError("现有 traceability.json 必须先审查迁移到 schema_version=2")
            if existing_trace.get("project") != project_id:
                raise InitError("不能通过重新初始化改变已有追溯账本的项目标识")
        today = date.today().isoformat()
        initialized_at = str(existing.get("initialized_at", today)) if existing else today
        date.fromisoformat(initialized_at)
        requirement_sources = destinations if args.copy_requirements else sources
        source_strings = [
            path.relative_to(target).as_posix()
            if path.is_relative_to(target) else path.as_posix()
            for path in requirement_sources
        ]
        expected = {
            "project": project_id,
            "name": project_name,
            "project_type": args.project_type,
            "requirements": {
                "mode": args.requirements_mode,
                "sources": source_strings,
            },
        }
        if existing and not args.overwrite_existing:
            differences = {
                key: (existing.get(key), value)
                for key, value in expected.items()
                if existing.get(key) != value
            }
            if differences:
                details = ", ".join(
                    f"{key}: {old!r} -> {new!r}" for key, (old, new) in differences.items()
                )
                raise InitError(
                    "已有 Harness 初始化选择与本次不同；如确认替换，请使用 "
                    f"--overwrite-existing。差异：{details}"
                )
        source_list = "\n".join(f"- `{value.replace('`', '')}`" for value in source_strings)
        if not source_list:
            source_list = "- 尚未提供现有需求资料。"

        texts = requirement_text(args.requirements_mode)
        harness_skill = f"{project_id}-harness"
        work_suffix = "engineering" if args.project_type == "code" else "work"
        work_skill = f"{project_id}-{work_suffix}"
        initial_handoff = f"{initialized_at}-harness-initialization.md"
        root_text = target.as_posix().replace("`", "")
        work_guidance = (
            f"代码实现、重构或审阅按需读取 [{work_skill}]"
            f"(.harness/skills/{work_skill}/SKILL.md)。"
            if args.project_type == "code"
            else f"非代码交付制作或审阅按需读取 [{work_skill}]"
            f"(.harness/skills/{work_skill}/SKILL.md)。"
        )
        tokens = {
            "__PROJECT_NAME__": project_name,
            "__PROJECT_NAME_JSON__": json.dumps(project_name, ensure_ascii=False),
            "__PROJECT_ID__": project_id,
            "__PROJECT_ROOT__": root_text,
            "__PROJECT_ROOT_JSON__": json.dumps(target.as_posix(), ensure_ascii=False),
            "__PROJECT_TYPE__": args.project_type,
            "__PROJECT_TYPE_LABEL__": "代码工程" if args.project_type == "code" else "非代码工程",
            "__REQUIREMENTS_MODE__": args.requirements_mode,
            "__REQUIREMENTS_SOURCES_JSON__": json.dumps(source_strings, ensure_ascii=False),
            "__REQUIREMENTS_SOURCE_LIST__": source_list,
            "__REQUIREMENTS_STATUS__": texts["status"],
            "__REQUIREMENTS_OPENING__": texts["opening"],
            "__FREEZE_STATUS__": texts["freeze"],
            "__FREEZE_OPENING__": texts["freeze_opening"],
            "__NEXT_ACTION__": texts["next"],
            "__NEXT_ACTION_JSON__": json.dumps(texts["next"], ensure_ascii=False),
            "__DATE__": initialized_at,
            "__UPDATED_DATE__": today,
            "__INITIAL_HANDOFF__": initial_handoff,
            "__HARNESS_SKILL_NAME__": harness_skill,
            "__WORK_SKILL_NAME__": work_skill,
            "__WORK_SKILL_GUIDANCE__": work_guidance,
        }
        common = TEMPLATE_ROOT / "common"
        handoff_content = render(
            common / ".harness/progress/handoffs/initialization.md", tokens
        )
        tokens["__INITIAL_HANDOFF_SHA256__"] = hashlib.sha256(
            handoff_content.encode("utf-8")
        ).hexdigest()
        trace = existing_trace or json.loads(
            render(common / ".harness/traceability.json", tokens)
        )
        project = json.loads(render(common / ".harness/project.json", tokens))
        if existing:
            # 用户已补充的介绍、创建日期和公开/Git边界不属于重新初始化应重置的数据。
            project = {**project, **existing, **expected, "root": target.as_posix()}
        library = load_template_library()
        tokens["__CURRENT_STATE_BLOCK__"] = library.render_current_block(trace)
        projections = {
            ".harness/progress/dashboard.md": library.render_dashboard(trace, project),
            ".harness/progress/tasks.md": library.render_tasks(trace),
            ".harness/progress/deliverables.md": library.render_deliverables(trace),
        }

        planned_files: list[str] = []
        for source, relative in template_files(args.project_type):
            planned_files.append(output_relative(relative, tokens).as_posix())
            render(source, tokens)
        planned_files.extend(projections)
        if args.create_gitignore:
            planned_files.append(".gitignore")
        planned_files.extend(path.relative_to(target).as_posix() for path in destinations)
        for relative in planned_files:
            destination = target / relative
            if destination.is_symlink() or not destination.resolve().is_relative_to(target):
                raise InitError(f"拒绝向软链接或项目外路径写入：{destination}")

        result = {
            "status": "预演" if args.dry_run else "已初始化",
            "target": str(target),
            "project": project_id,
            "project_type": args.project_type,
            "create_gitignore": args.create_gitignore,
            "requirements_mode": args.requirements_mode,
            "created": [],
            "preserved": [],
            "overwritten": [],
            "planned_files": sorted(set(planned_files)),
            "git": [],
            "structure": {
                **({
                    ".editorconfig": "通用编辑器格式约定",
                    "README.md": "面向读者的项目说明入口",
                } if args.project_type == "code" else {}),
                **({
                    ".gitignore": "项目自定的 Git 忽略规则（已有内容保持不变）",
                } if args.create_gitignore or (target / ".gitignore").is_file() else {}),
                **STRUCTURE,
            },
        }
        if args.dry_run:
            result["git"].append(
                "将初始化本地 Git 仓库" if args.git_mode == "init" else "将跳过 Git 初始化"
            )
            if args.remote_url:
                result["git"].append(
                    f"将配置远端 {args.remote_name}={args.remote_url}，不会联网"
                )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print_human(result)
            return 0

        target.mkdir(parents=True, exist_ok=True)
        trusted_checks: dict[str, bool] = {}
        for source, relative in template_files(args.project_type):
            destination = target / output_relative(relative, tokens)
            content = render(source, tokens)
            relative_output = destination.relative_to(target).as_posix()
            if relative_output == ".harness/project.json":
                content = json.dumps(project, ensure_ascii=False, indent=2) + "\n"
            if relative_output.startswith(".harness/tools/") and destination.suffix == ".py":
                existing_matches = False
                if destination.exists() and not args.overwrite_existing:
                    try:
                        existing_matches = destination.read_text(encoding="utf-8") == content
                    except (OSError, UnicodeError):
                        existing_matches = False
                trusted_checks[relative_output] = (
                    not destination.exists() or args.overwrite_existing or existing_matches
                )
            write_text(
                destination, content,
                args.overwrite_existing and not protected_record(Path(relative_output)),
                result,
            )
        for relative, content in projections.items():
            write_text(target / relative, content, args.overwrite_existing, result)

        ignore_path = target / ".gitignore"
        if args.create_gitignore:
            write_text(ignore_path, "# 按项目需要填写 Git 忽略规则。\n", False, result)
        elif ignore_path.exists():
            result["preserved"].append(str(ignore_path))

        if args.copy_requirements:
            for source, destination in zip(sources, destinations):
                if source == destination.resolve():
                    result["preserved"].append(str(destination))
                elif destination.exists() and not args.overwrite_existing:
                    result["preserved"].append(str(destination))
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    existed = destination.exists()
                    shutil.copy2(source, destination)
                    result["overwritten" if existed else "created"].append(str(destination))

        configure_git(args, target, result)
        result["checks"] = validate_generated(target, trusted_checks)
        if any(
            check["exit_code"] not in (0, None)
            for check in result["checks"]
        ):
            result["status"] = "已写入，但自检失败"

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 1 if result["status"] == "已写入，但自检失败" else 0
    except (InitError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        payload = {"status": "失败", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"初始化失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
