---
name: __HARNESS_SKILL_NAME__
description: 维护 __PROJECT_NAME__ 的 Harness 文档、需求冻结、进度投影、记忆、交接和协作约定。用于更新 .harness、AGENTS.md 或跨会话治理状态；不用于替代项目自身的实现和验证。
---

# __PROJECT_NAME__ Harness 维护

开始前读取根 `AGENTS.md`、`.harness/README.md`、用户看板 `progress/dashboard.md` 和续接状态 `progress/current.md`，其余按任务加载。

- 用户最新明确决定优先；未确认的问题保持开放。
- 用户输入只放 `document/`；技术设计放 `design/`，冻结实施与验收方案放 `plans/`，报告和原始输出放 `validation/`，被替代快照放 `history/`。
- `.harness/traceability.json` 是唯一关联与状态事实源，维护真实的需求或决策、设计、方案、任务、验证报告、证据到状态投影链。
- 状态变化先记录实际证据，再更新追溯源，运行 `sync_views.py --write`；不手工改生成的看板、任务清单、交付映射或当前状态块来绕过检查。
- 看板最多 50 行、4000 字符，只概览阶段、完成度、当前动作、风险、能力边界和关键入口。完整任务和运行明细留在各自事实源。
- 工作包报告固定为 `validation/<任务号>/report.md`；各轮证据独立，失败和中断不可被后续通过覆盖。实现、验收条件与实际证据一致才可标记 verified 或 complete。
- 交接和历史只追加；交接登记 SHA-256 并维护最新指针，迁移保留原文哈希及旧新映射，不修改旧交接或证据。
- 只有经过确认、跨任务有用的稳定事实才写入 `memory/project.md`；即时进度保存在 `progress/`。
- `.harness/` 和 `AGENTS.md` 默认可以通过 Git 同步，是否公开由用户自行决定；不自行暂存、提交、推送、发布或公开协作材料。
- 保留已有 Git 忽略配置，不添加 Harness 相关规则；同步前检查资料的敏感性。
- 产品公开内容不混入开发计划、验证过程、交接、内部评估或 Harness 路径；实际发布物须单独指定检查。
- 结束前运行 `.harness/tools/check.py` 与 `sync_views.py`，按真实检查结果报告限制，不将资料自检当作产品测试。
