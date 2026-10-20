# __PROJECT_NAME__ 稳定项目记忆

这里只保存跨任务有用且不易变化的事实；需求、即时进度、猜测和原始对话不放在这里。

- 项目根目录：`__PROJECT_ROOT__`
- 项目类型：`__PROJECT_TYPE__`
- Harness 入口：`AGENTS.md` 与 `.harness/README.md`
- 文档导航：`.harness/document/README.md`
- 技术设计与契约：`.harness/design/`
- 唯一关联事实源：`.harness/traceability.json`
- 当前状态：`.harness/progress/current.md`
- 协作资料：`.harness/` 和 `AGENTS.md` 默认可以通过 Git 同步，是否公开由用户自行决定
- Git 提交边界：未得到用户当前明确的提交指令时，只准备和验证，不自行提交

长期事实经过确认后再追加。密钥、令牌、个人敏感信息和完整敏感载荷不得写入记忆。
