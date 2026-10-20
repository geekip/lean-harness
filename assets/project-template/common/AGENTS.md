# __PROJECT_NAME__ 项目协作约定

项目根目录为 `__PROJECT_ROOT__`。本文件是 AI/Agent 协作的常驻入口；其余资料按任务需要读取，避免一次性加载全部文档。

## 开始工作

1. 以用户最新明确指令为准，先检查实际工作区与相关材料。
2. 阅读 [.harness/README.md](.harness/README.md)、[用户看板](.harness/progress/dashboard.md)和[工作续接状态](.harness/progress/current.md)。
3. 需要推进任务时再读[任务清单](.harness/progress/tasks.md)；需要理解需求时从[文档导航](.harness/document/README.md)选择最小阅读集合。
4. Harness 维护读取 [__HARNESS_SKILL_NAME__](.harness/skills/__HARNESS_SKILL_NAME__/SKILL.md)；__WORK_SKILL_GUIDANCE__
5. 修改追溯事实后运行 `python .harness/tools/sync_views.py --write` 更新投影，再运行 `python .harness/tools/check.py`。这里的 `python` 代表本机 Python 3.10+，可替换为 `python3` 或 Windows 的 `py -3`。

## 事实、范围与权限

- 用户最新明确决定优先；已冻结需求定义目标；实际文件与当前验证证据说明实现状态。冲突必须暴露并修订事实源。
- Harness 负责需求澄清、任务分析、上下文、权限、记忆、文档和健康检查，不运行模型，也不替代 Git、产品测试或 CI。
- `.harness/` 和 `AGENTS.md` 默认可以通过 Git 同步，是否公开由用户自行决定；这不授权自动暂存、提交、推送或公开仓库。
- 不为 Harness 添加 Git 忽略规则或反忽略规则，保留项目已有 `.gitignore`；同步前检查资料，不写入密钥、令牌或其他敏感内容。
- 不因文档、目录、占位内容或 Harness 自检通过而宣称功能已经实现。
- 不默认执行联网安装、远程写入、提交、推送、发布或部署；这些动作需要用户当前任务明确覆盖。
- 保留用户已有内容，只修改当前任务所需文件；未经明确授权不得覆盖既有文件。

## 信息与状态

- `document/` 仅保存用户需求、用户决策、讨论及原始材料；`design/` 保存技术设计和契约；`plans/` 只保存已冻结的实施与验收方案。
- `.harness/traceability.json` 是唯一关联与状态事实源；开发工作形成「用户需求或决策 → 已批准设计 → 冻结方案 → 任务 → 验证报告 → 原始证据 → 状态投影」。
- 任务状态为 `planned / in_progress / blocked / verified / complete`；实现、验收条件和实际证据一致时才能进入后两者，不等同于已发布。
- `progress/dashboard.md` 是一到两屏的人类摘要；`tasks.md`、`deliverables.md` 和 `current.md` 状态块由事实源生成，不维护另一套计数、NEXT 或能力声明。详细恢复信息写在 `current.md` 人工区。
- 项目真实创建时间未知时不使用初始化时间替代；没有确认的任务范围或版本计划时不编造进度。
- 根 README 仅面向产品用户；公开文档、源码、构建文件及发布包不得带入开发计划、测试过程、交接、内部评估或 Harness 路径。不为开发过程资料创建公开文档目录。

## 验证与交接

- 先运行与变更最接近的验证，再运行 Harness 自检；两者不能互相替代。
- 每个工作包使用 `validation/<任务号>/report.md`；每轮原始证据放同目录的 `runs/<轮次号>/`，报告保留候选、环境、命令、退出状态、指标、失败/中断和适用边界。
- 验证记录和交接只追加不覆盖，并在追溯源登记 SHA-256。后续通过不能抹去失败；迁移不能改写旧交接和原始证据，需追加新交接解释变化。
- `history/` 只存明确标记非当前事实源的快照，保留旧新路径映射和哈希。活动引用必须指向当前文件。
- 未配置或提供发布物时，自检不代表已检查发布包；使用 `check.py --release <路径>` 检查实际发布物。
