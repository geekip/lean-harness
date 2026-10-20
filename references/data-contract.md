# 追溯与验证数据契约

持久化追溯条目中的文件路径使用项目根目录相对路径与 `/`，不能越出项目或通过符号链接逃逸；导入时的原始来源路径不受此表示方式限制。关联 ID 在各类集合中唯一；不要把说明性示例当成实际项目记录。

## 项目元数据

`.harness/project.json` 使用 `schema_version: 2`、`layout_version: 2`。`summary` 是用户可读的简短概述；`created_at` 是项目真实创建时间，未知用 `null`；`initialized_at` 是资料初始化时间，两者不可混淆。

`boundaries` 配置：

```json
{
  "git": "user-managed",
  "public_paths": [],
  "release_paths": []
}
```

`user-managed` 不要求忽略协作资料，检查报告实际 Git 边界；只有用户明确选择排除后才用 `excluded`。`public_paths` 补充项目公开内容路径；`release_paths` 登记实际发布目录或档案。空发布列表不是发布检查通过。

## 唯一追溯源

`.harness/traceability.json` 使用 `schema_version: 2`。初始集合为空，后续只登记真实记录：

| 集合 | 核心字段 |
|---|---|
| `requirements` | `id`、`source`（document 内）、`status`（draft/frozen/superseded）、`acceptance_criteria` |
| `decisions` | `id`、`source`（document 内）、`status`（draft/confirmed/superseded） |
| `designs` | `id`、`path`（design 内）、`requirements`、`decisions`、`status`（draft/approved/superseded） |
| `plans` | `id`、`path`（plans 内）、`designs`、`status`（frozen/superseded）、`acceptance_criteria` |
| `tasks` | `id`、`title`、`kind`（development/governance）、`status`、`requirements`、`decisions`、`designs`、`plan`、`tests`、`artifacts`、`depends`、`report` |
| `tests` | `id`、`task`、`status`（not_run/pass/fail/interrupted）、`run` |
| `artifacts` | `path`、`status`（planned/implemented/verified） |
| `capabilities` | `id`、`summary`、`status`（verified/unimplemented）、`tasks` |
| `versions` | `id`、`title`、`tasks`；只登记真实版本或里程碑计划 |
| `validation_runs` | `task`、`run_id`、`record`、`sha256`；每轮追加，不覆盖 |
| `handoffs` | `path`、`sha256`、`created_at`；按交接顺序追加，不覆盖 |

上述引用集合使用 ID，唯 `tasks.artifacts` 使用登记过的交付物路径。任务状态为 `planned / in_progress / blocked / verified / complete`。`plan`、`report`、`tests.run` 未存在时用 `null`；报告一旦存在，其固定路径为 `.harness/validation/<任务号>/report.md`。

`state` 包含 `phase`、`action`、`risks`（字符串列表）、`scope_confirmed`（布尔值）、`updated_at`、`next_task`、`current_work_package`、`latest_handoff`。任务指针未确定时用 `null`，最新交接指向 `handoffs` 的最后一条。范围确认不是自动验收通过。

开发任务必须关联冻结需求或已确认用户决定、已批准设计与已冻结方案，且它们相互对应。`verified/complete` 还必须由该任务的实际测试、当前候选和证据支持；已验证能力只能由已验收开发任务支撑。追溯校验不允许悬空引用或依赖环。

## 验证记录

先运行实际验收并保存原始输出，再调用：

```text
python .harness/tools/record_validation.py --task TASK-001 --run-id run-001 --input "<验证输入JSON>"
```

该命令导入证据，不运行输入中的命令、不替用户确认验收条件，也不自动更新任务为通过。输入 JSON 包含 `candidate`、`environment`、`commands`、`status`、`metrics`、`boundaries`，以及使用 `source` 指定已存在原始文件的 `evidence`，例如 `"evidence": [{"source": "实际日志路径"}]`。相对来源路径相对于项目根；候选文件须显式提供执行时的真实 SHA-256，且精确覆盖该任务的交付物，不能用记录时刚算出的哈希冒充执行时身份。

工具生成任务号、轮次号、记录时间、验收条件摘要 `scope_sha256`，将来源证据复制到新轮次并记录实际路径与哈希。每轮生成的 `record.json` 至少包含：

```json
{
  "task": "TASK-001",
  "run_id": "run-001",
  "candidate": {
    "identity": "可复现识别的候选版本或提交及脏工作区说明",
    "artifacts": [{"path": "实际交付物路径", "sha256": "实际文件SHA-256"}]
  },
  "environment": {"platform": "实际运行环境"},
  "commands": [{"argv": ["实际命令", "参数"], "exit_code": 0}],
  "status": "pass",
  "scope_sha256": "实际关联需求、设计、方案及其内容的SHA-256摘要",
  "metrics": {},
  "boundaries": ["实际覆盖与未覆盖边界"],
  "evidence": [{"path": ".harness/validation/TASK-001/runs/run-001/evidence/001-实际证据文件", "sha256": "实际文件SHA-256"}],
  "recorded_at": "实际记录时间"
}
```

上例只是字段说明，示例字符串不是有效证据。`pass` 要求非空实际证据及全部命令退出 0；失败与中断也应保存已有日志或中断说明，`interrupted` 可保留未产生退出状态的 `null`。`metrics` 即使无数值指标也保留对象；环境不得空白，边界须说明适用性。输入清单、资源数据和哈希应与本轮原始输出一起保存。

报告汇总同一工作包所有已登记轮次，包含失败与中断次数和原始证据链接。若通过后修改实现或验收范围，旧通过只属于旧候选与旧边界；必须重新验证，而不能只改报告结论。重验前先将已验收任务退回 `in_progress`，同步失效的能力及交付物状态，旧证据仍保留。哈希检查是本地一致性检查，不是防篡改存储。
