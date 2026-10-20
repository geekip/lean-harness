# __PROJECT_NAME__ Harness

这是项目的本地工程治理资料，用于需求澄清、任务分析、上下文选择、工具权限、记忆、进度与健康检查。Harness 不运行大模型，也不替代 Git、测试框架或 CI。

本工程由纯 Skill 模板生成。使用技能的 AI 负责遵循协作约定、引导澄清、分析任务和维护文档；Python 脚本负责初始化与本地检查，实际实现由开发人员或外部 AI 编码工具完成。

工具的操作边界写在根 `AGENTS.md` 和 [协作规范](design/engineering.md) 中；这些约定不提供自动权限拦截。

`.harness/` 和 `AGENTS.md` 默认可以通过 Git 同步，是否公开由用户自行决定。初始化不会自动暂存、提交、推送或公开仓库，资料中的敏感内容仍需保护。

非代码工程不创建 `.gitignore`；代码工程仅在用户选择创建且文件不存在时创建，已有文件保持不变。不向 `.gitignore` 添加 Harness 相关内容，也不调整其他 Git 忽略配置。

## 使用入口

1. 从根 [AGENTS.md](../AGENTS.md) 了解常驻约定。
2. 从 [用户看板](progress/dashboard.md) 了解阶段、完成度、当前动作、风险与能力边界；详细恢复信息在 [续接状态](progress/current.md)。
3. 按任务选择 [用户资料](document/README.md)、[设计](design/README.md)、[冻结方案](plans/README.md) 或 [验证](validation/README.md)，不要一次加载所有资料。
4. [任务](progress/tasks.md)与[交付映射](progress/deliverables.md)均由[唯一追溯源](traceability.json)生成；先改事实源，再用 `sync_views.py --write` 更新视图。
5. 完成治理改动后运行：

```text
python .harness/tools/check.py
python .harness/tools/sync_views.py
```

以上命令在项目根目录运行，需要 Python 3.10+。`python` 可替换为本机的 `python3` 或 Windows 的 `py -3`；脚本仅使用标准库，支持通过解释器在 Windows、macOS 和 Linux 运行。

## 目录职责

```text
.harness/
├── README.md       # 使用入口与目录说明
├── project.json    # 项目概况、初始化选择和公开/Git/发布边界
├── traceability.json # 需求、设计、方案、任务、测试与状态的唯一关联事实源
├── document/       # 用户需求、用户决策、讨论及原始材料
├── design/         # 技术架构、格式规范和接口契约
├── plans/          # 已冻结的实施与验收方案
├── validation/     # 测试策略、工作包报告与各轮原始证据
├── history/        # 明确非当前事实源的快照、迁移映射与哈希
├── memory/         # 已确认且跨任务有用的项目事实
├── progress/       # 当前状态投影、任务、交付映射及不可覆盖的交接
├── skills/         # 按需读取的项目协作和专业工作方法
└── tools/          # Python 结构自检和追溯校验，不代替项目测试
```

开发工作使用「用户需求或决策 → 已批准设计 → 冻结方案 → 任务 → 验证报告 → 原始证据 → 当前状态」链。尚无真实内容的集合保持空白，不因目录存在编造需求、任务或验收。

验证报告固定在 `validation/<任务号>/report.md`，各轮证据与报告同属一个工作包目录。失败、中断和旧交接不可被后续结果覆盖；有追溯价值的被替代资料按[历史说明](history/README.md)归档。

根 README 与公开文档仅面向产品用户，不承载内部过程资料或本目录路径。Git 同步由用户决定，不代表这些资料应进入产品源码、构建文件或发布包。未提供实际发布物时，自检会注明未检查发布包。
