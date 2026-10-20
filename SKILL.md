---
name: lean-harness
description: 初始化或维护与项目类型、编程语言无关的 Harness 工作区。用于需求澄清与冻结、分层组织设计和计划、维护可追溯验证与进度、迁移协作资料及检查公开边界。
---

# Lean Harness

使用本技能准备和治理项目。Harness 不运行大模型，不替代 Git、项目测试或 CI，也不因初始化而授权产品实现、提交、推送或发布。

本包是纯 Skill：使用技能的 AI 负责引导、分析和资料维护；Python 工具负责初始化、投影及一致性检查，无外部 Harness 引擎。

工具只用 Python 3.10+ 标准库，适用于 Windows、macOS 和 Linux；示例中的 `python` 可替换为 `python3` 或 `py -3`，不依赖 Shell 脚本。Git 操作才要求安装 Git。

## 信息职责与事实源

- `document/` 只保存用户需求、用户决策、需求讨论和用户原始材料。
- `design/` 保存技术架构、格式规范、接口契约；`plans/` 只保存已确认冻结的实施与验收方案。
- `progress/` 保存人类看板、工作续接、任务和交付投影、不可覆盖的交接；`validation/<任务号>/` 保存该工作包的结论报告及各轮原始证据。
- `history/` 只保存注明非当前事实源的被替代快照；`memory/` 保存稳定事实；`skills/` 和 `tools/` 分别保存项目方法和工具。
- `.harness/traceability.json` 是需求、设计、方案、任务、测试、交付物与状态关联的唯一机器事实源。看板和清单是它的投影，不另建账本。

开发工作必须可追溯为「用户需求或决策 → 已批准设计或契约 → 冻结实施方案 → 任务 → 验证报告 → 原始证据 → 当前状态」。治理工作不强造技术设计；只有实现、验收条件和实际证据一致，才能标记 `verified` 或 `complete`。失败和中断记录不得被后来的通过结果覆盖。

根 `README.md` 面向产品用户；公开文档、源码、构建文件和发布包不得混入开发过程材料或 Harness 路径。是否通过 Git 同步协作资料与是否将其作为产品公开内容是不同决定。

## 引导式初始化

写入前，将目标解析为绝对路径，并检查已有文件和 Git 边界。复用用户已经给出的答案，只询问仍未确定的选择：

1. 这是代码工程还是非代码工程？
2. 仅对代码工程询问：是否在缺失时创建 `.gitignore`？未选择时不创建；非代码工程不创建，已有文件始终保留原样。
3. 是否初始化 Git？如果初始化，再询问是否配置可选的远端地址和远端名称；不得连接远端。
4. 是否已有需求文档？推荐将原始输入放在 `.harness/document/sources/`，将评审后的基线放在 `.harness/document/requirements.md`；询问是引用原文件还是复制导入。目录位置不提供保密保护，敏感资料是否导入由用户决定。
5. 是否通过引导问答澄清并冻结需求？推荐选择**是**。未回答事项保持开放，不得把实现建议写成冻结需求。

初始化前读取 [references/initialization.md](references/initialization.md)，了解选项、需求冻结和目录树。

仅在上述选择明确后运行确定性初始化器：

```text
python "<技能目录>/scripts/init_project.py" --target "<项目绝对路径>" --project-type code --requirements-mode qa --git-mode skip --json
```

只有代码工程且用户选择创建 `.gitignore` 时才添加 `--create-gitignore`。每个现有需求来源添加 `--requirements-source <路径>`；复制导入需用户选择后加 `--copy-requirements`；远端地址需用户提供后加 `--remote-url <地址>`。`--overwrite-existing` 只用于已明确授权替换的模板文件，不得覆盖已有 `.gitignore`、验证证据、历史快照或交接。

代码工程确保根目录存在 `.editorconfig`、`AGENTS.md` 和 `README.md`：缺失时创建，默认保留已有文件；`.gitignore` 按上述选择处理。非代码工程创建协作入口 `AGENTS.md`，不创建 `.gitignore`、`.editorconfig` 或 `README.md`。不向 `.gitignore` 写入任何 Harness 忽略规则、反忽略规则或管理区块。

`.harness/` 和 `AGENTS.md` 默认可以通过 Git 同步，意为模板不主动排除这些文件，是否公开由用户自行决定。初始化器不覆盖现有或全局忽略规则，不清理以前用户工程中的 Harness 区块，也不会自动暂存、提交、推送或改变仓库可见性；可同步不等于已被跟踪或已公开。

初始化后：

1. 初始化器运行本次新建或与可信模板一致的自检工具；目标已有不同版本时，先审阅再运行，不能自动信任。
2. 说明 Git 初始化、可选远端和 `.gitignore` 的实际处理结果；已有忽略规则继续生效，不擅自改写文件或增删 Git 索引记录。
3. 展示实际创建或保留的文件树，并解释每个 Harness 顶层区域的作用，不能只回复“初始化成功”。
4. 如果选择问答方式，开始或提出第一轮需求澄清，了解项目概述、实际创建时间和已有版本计划等基础信息；未知信息保留待确认，不阻塞初始化。完成初始化不等于需求已经冻结。

## 治理已有项目

在指定项目中定位 `.harness/`；可向上查找最近的 `project.json` 或 `.harness/README.md`。先读项目 `AGENTS.md`、`.harness/README.md`、`.harness/progress/dashboard.md` 和 `progress/current.md`，其余按任务加载。

继续工作时读 [references/lifecycle.md](references/lifecycle.md)；需要编写追溯记录、验证输入或边界配置时读 [references/data-contract.md](references/data-contract.md)。旧布局迁移前必须读 [references/migration.md](references/migration.md)，不能通过重跑初始化器生成第二套活动事实源。

看板仅用一到两屏回答阶段、完成度、当前动作、风险、已验证能力、未实现边界和关键入口。详细恢复信息写入 `progress/current.md` 的人工维护区；任务历史和运行明细留在各自事实源。更新事实源后生成投影，再检查：

维护完成后，运行生成的本地检查工具，并按真实结果更新当前状态和交接：

```text
python .harness/tools/check.py
python .harness/tools/sync_views.py
```

`sync_views.py` 默认只检查；明确更新投影时加 `--write`。检查覆盖目录、活动链接、追溯、状态投影、交接哈希、公开残留、Git 实际边界和看板可读性。发布包用 `check.py --release <路径>` 指定；未配置或未提供时必须报告未检查发布包，不宣称完整发布安全。检查通过不等于产品验收通过。
