# CodeGraph 与 agentmemory 轻量集成设计

## 目标

在不改变现有开发启动门槛和记忆体系的前提下，引入 CodeGraph 作为“当前代码结构”查询工具，并在少数稳定边界把结构影响摘要保存到 agentmemory。

成功标准：

- Agent 能按问题类型选择 CodeGraph、agentmemory 或源代码，而不是把全部上下文都塞入提示词。
- CodeGraph 不进入启动控制平面；图谱不可用时，启动、编辑、测试、提交和常规记忆捕获仍能继续。
- agentmemory 不保存 CodeGraph 的完整数据库、源码正文或原始边集合，只保存有来源、可判定陈旧的历史摘要。
- 试点能量化时间、token 和正确性收益，并能独立关闭 CodeGraph 或事件桥。

## 非目标

- 不建设统一检索 API、统一排序器或统一向量库。
- 不修改 agentmemory 的 iii 引擎、存储模型或现有自动捕获生命周期。
- 不让 CodeGraph 替代源代码、测试、Git 或 `~/AI开发执行规范.md`。
- 不在每次保存文件、每次工具调用或每次图查询后写入记忆。
- 不承诺图谱能完整表达动态调用、反射、生成代码、运行时注入或跨服务行为。

## 核心原则

系统继续分为控制平面和知识平面。CodeGraph 只作为知识平面中的一个独立、可选的结构查询源。

```text
控制平面（保持不变）
ai_dev_spec_bootstrap
  -> 核对 pwd / Git 根 / worktree / branch / status / port
  -> 召回项目记忆
  -> 获取任务上下文

运行时知识源
CodeGraph   -> 当前代码结构、符号关系、调用链、影响范围
agentmemory -> 历史决策、过往会话、偏好、经验、commit 上下文
源码与测试  -> 当前实现正确性的最终权威
本地开发规范 -> 开发行为的最终权威
```

任何知识源之间发生冲突时，按以下优先级处理：

1. 开发行为遵循本地开发规范。
2. 当前实现事实遵循当前 worktree 的源代码、配置和测试。
3. Git 提供版本与提交边界事实。
4. CodeGraph 是可验证的派生结构索引。
5. agentmemory 中的桥接摘要是历史快照，不是当前事实。

## 组件与所有权边界

### CodeGraph MCP

CodeGraph 使用独立 MCP 配置和独立工具命名空间。它负责索引仓库、定位符号、查询 callers/callees、跟踪结构路径和估算受影响符号。它不负责解释历史决策，也不直接写入 agentmemory。

### agentmemory

agentmemory 继续通过现有 hooks、REST 和 MCP 捕获会话与决策。它负责回答“过去发生了什么”和“为什么这样做”。已有的捕获、压缩、整合和遗忘流程保持不变。

### 路由策略

路由首先由 Agent 指令完成，不增加中间代理。每次查询先识别问题属于结构事实、历史事实还是混合问题，再调用对应 MCP。只有返回结果不足以支持下一步时才扩展查询。

| 问题类型 | 首选来源 | 典型查询 | 校验方式 |
|---|---|---|---|
| 当前符号定义、引用、调用关系 | CodeGraph | 定义在哪里、谁调用它、它调用谁 | 打开相关源码 |
| 修改影响范围 | CodeGraph | 哪些符号、入口、测试可能受影响 | `rg`、源码和相关测试 |
| 过去决策和原因 | agentmemory | 为什么选择该接口、此前踩过什么坑 | commit、文档和当前代码 |
| 用户偏好、项目约定、会话续接 | agentmemory | 上次做到哪里、约定了什么 | 当前任务与项目文档 |
| 混合问题 | 两者分别查询 | 当前调用链与历史设计原因 | 合并后以源码为准 |
| 精确文本、动态配置、生成内容 | 源码与工具链 | 字符串、配置键、运行时行为 | `rg`、构建或运行测试 |

禁止仅因为 CodeGraph 返回“无结果”就断言符号或依赖不存在。无结果、低置信度、索引陈旧或语言不受支持时，必须回退到源码搜索。

## 独立 MCP 配置

CodeGraph 和 agentmemory 作为两个并列 MCP server 注册到宿主 Agent：

- `agentmemory` 保持现有地址、启动方式和工具配置。
- `codegraph` 使用自身支持的本地启动命令或 MCP endpoint，并把索引目录放在其独立数据目录。
- 两者不共享数据库、端口、进程生命周期或健康检查。
- CodeGraph 配置不得写入 `ai_dev_spec_bootstrap` slot，也不得成为 SessionStart gate 的必检项。
- CodeGraph 的仓库根目录必须使用启动门槛核验后的真实 worktree，不能默认使用主 worktree 或 `main`。
- 首个试点阶段只配置一个明确仓库；多仓库自动发现和跨仓库图查询不在本阶段范围。

宿主指令只增加最小路由约定和失败回退，不注入完整工具说明。实际 MCP 命令、参数名和健康检查以安装时锁定的 CodeGraph 版本为准，并在实施计划中通过该版本的 README 和工具自描述确认，避免把易变 CLI 写死为架构契约。

## 运行时查询流程

### 结构问题

1. 使用 CodeGraph 做一次窄查询，限定入口符号、文件或关系方向。
2. 最多返回 25 个符号和 30 条关系；优先同一模块、直接一跳和测试相关节点。
3. 打开最相关源码核验定义与关系。
4. 只有一跳结果不足时，再扩展一次；禁止无界图遍历。

### 历史问题

1. 使用 agentmemory 查询当前 project/worktree/branch 相关内容。
2. 默认最多取 5 条高相关结果。
3. 涉及实现事实时，用当前源码或 commit 校验。

### 混合问题

1. 分别查询 CodeGraph 与 agentmemory，不把一个系统的原始结果复制到另一个系统再检索。
2. 合并时明确区分“当前结构”和“历史原因”。
3. 若两者矛盾，报告陈旧历史或索引风险，并以源码和测试为准。

单个任务的初始检索预算为：CodeGraph 不超过约 3000 tokens，agentmemory 不超过约 2000 tokens，合并给 Agent 的摘要不超过约 4500 tokens。达到预算后应先阅读已定位的源码，而不是继续扩大检索。

## 克制事件桥

### 触发条件

事件桥只在以下边界尝试运行：

1. 成功创建 Git commit 后。
2. 宿主发出 `TaskCompleted`，且本任务产生了已验证的代码修改。
3. 用户明确确认一项架构决策，且该决策值得跨会话保留。

普通文件保存、格式化、测试运行、MCP 调用、Agent 中间步骤和失败提交都不得触发桥接。相同 `event_id` 必须幂等，重复事件不得产生重复记忆。

commit 事件优先于 `TaskCompleted`。如果当前任务的全部变更已经由同一 HEAD 的 commit 摘要覆盖，`TaskCompleted` 不再写入第二条摘要；只有任务还包含未提交且已验证的代码变更时才单独桥接。架构决策可以与 commit 共存，因为它保存的是明确决策而不是重复影响列表。

### 数据流

```text
稳定边界事件
  -> 读取当前 Git 身份与变更文件
  -> 核对 CodeGraph 索引来源和新鲜度
  -> 对变更文件做一次有界影响查询
  -> 生成并验证有界摘要
  -> 通过 agentmemory 现有保存接口写入一条历史快照
```

commit 事件使用该 commit 相对第一父提交的变更；首次提交使用空树。`TaskCompleted` 使用本任务记录的、尚未被 commit 摘要覆盖的变更文件，并绑定当前 HEAD。明确架构决策使用当前 HEAD，同时标记 worktree 是否有未提交修改。

`event_id` 使用确定性 SHA-256：commit 事件散列 `schema + event_type + commit_sha`；`TaskCompleted` 散列 `schema + event_type + host_task_id + commit_sha + worktree_state_hash`；架构决策散列 `schema + event_type + commit_sha + normalized_decision`。原始 patch 和源码内容只参与本地散列，不进入记忆。

### 摘要结构

桥接记录使用以下逻辑结构；实现可映射为 agentmemory 的 content、concepts、files 和 metadata，但字段语义不得丢失：

```json
{
  "schema": "codegraph-impact-digest/v1",
  "event_type": "commit|task_completed|architecture_decision",
  "event_id": "stable-id",
  "captured_at": "RFC3339 timestamp",
  "project_root": "canonical absolute git root",
  "worktree_root": "canonical absolute worktree root",
  "branch": "branch name or detached:<short-sha>",
  "commit_sha": "40-character git SHA",
  "dirty_worktree": false,
  "worktree_state_hash": "SHA-256 or null",
  "changed_files": ["repo-relative/path"],
  "affected_symbols": [
    {"symbol": "qualified name", "file": "repo-relative/path", "relation": "changed|caller|callee|dependent", "confidence": "high|medium|low"}
  ],
  "affected_tests": ["repo-relative/path or qualified test name"],
  "decision": "confirmed decision or null",
  "summary": "bounded human-readable historical digest",
  "graph_provenance": {
    "codegraph_version": "exact version",
    "index_id": "index identifier or content hash",
    "indexed_commit_sha": "40-character git SHA or null",
    "indexed_worktree_state_hash": "SHA-256 or null",
    "indexed_at": "RFC3339 timestamp or null",
    "query_kind": "impact/affected query name",
    "freshness": "exact|behind|unknown"
  }
}
```

字段上限：

- `changed_files` 最多 20 项；超过时保留直接修改的入口、公共接口和测试，并在 `summary` 写明省略数量。
- `affected_symbols` 最多 12 项，优先 changed、直接 caller/callee 和公共边界。
- `affected_tests` 最多 8 项。
- `decision` 最多 600 个 Unicode 字符，仅允许保存用户明确确认的内容。
- `summary` 最多 800 个 Unicode 字符。
- 单条序列化记录最多 12 KiB；超限时按低置信度符号、间接关系、测试、文件的顺序裁剪，必填 provenance 不得裁剪。

`worktree_state_hash` 仅在 worktree 有未提交变更时生成，覆盖 staged diff、unstaged diff 和相关未跟踪文件的内容摘要。它只用于新鲜度与幂等判断，不等同于 commit，也不得被反向展开。CodeGraph 若不能提供对应的索引状态摘要，则 `indexed_worktree_state_hash` 为 `null`。

禁止写入源码正文、patch 正文、完整调用图、原始边列表、环境变量值、密钥、token、`.env` 内容或第三方私有数据。文件路径只保存仓库相对路径；绝对路径仅用于本地 worktree 身份字段，写入前必须确认不包含凭据。

### 新鲜度与冲突处理

- clean worktree 中，`indexed_commit_sha == commit_sha` 时标记 `exact`。
- dirty worktree 中，只有 `indexed_commit_sha == commit_sha` 且 `indexed_worktree_state_hash == worktree_state_hash` 时标记 `exact`；HEAD 相同本身不足以证明索引包含未提交修改。
- 索引明确落后时标记 `behind`，只允许保存直接变更文件和已由源码核验的符号；不得把推测的间接影响写成事实。
- 无法确定索引 commit 时标记 `unknown`。commit 和 `TaskCompleted` 事件跳过桥接写入；架构决策仍可单独保存，但 `affected_symbols` 和 `affected_tests` 必须为空。
- 查询时，桥接摘要必须显示 `captured_at`、`commit_sha` 和 `freshness`。如果当前 HEAD 不同，Agent 应把它表述为历史证据，并重新查询 CodeGraph 或源码。
- CodeGraph 与源码冲突时，不修补摘要来伪装一致；保留来源信息，当前任务以源码为准。

## 失败隔离与熔断

CodeGraph 索引、健康检查或查询失败时：

- 运行时查询立即回退到 `rg`、语言工具链、源码和测试。
- 事件桥记录一条不含敏感数据的本地诊断并跳过本次图谱摘要；不得阻止 commit、TaskCompleted 或 agentmemory 常规捕获。
- 同一会话连续 3 次失败后关闭 CodeGraph 查询和事件桥 15 分钟，或直到用户显式重试；agentmemory 保持工作。
- 恢复后先做健康检查和一个窄查询，不自动重放所有遗漏事件。
- 桥接写入 agentmemory 失败时，最多重试 1 次；仍失败则丢弃本次派生摘要，不创建旁路数据库。

熔断状态属于本地运行状态，不保存成项目记忆。任何失败都不得改变 `ai_dev_spec_bootstrap` 的通过条件。

## 隐私与遥测

- 默认只使用本地 CodeGraph 和本地 agentmemory endpoint。
- 安装时必须记录 CodeGraph 是否上传源码、图数据或遥测；存在远程传输时默认关闭，并在启用前单独获得用户确认。
- MCP 日志和桥接诊断不得打印源码正文、prompt 全文、凭据或 `.env` 内容。
- 仓库忽略规则和现有敏感文件策略继续适用；CodeGraph 索引目录不得被误提交。
- 若 CodeGraph 不能证明索引完全本地，试点只允许使用无敏感数据的测试仓库。

## 测试与基准

### 功能测试

1. 结构问题只调用 CodeGraph，并能定位后续源码核验位置。
2. 历史问题只调用 agentmemory，不启动无关图遍历。
3. 混合问题分别返回当前结构和历史原因，冲突时源码优先。
4. CodeGraph 关闭、索引损坏和超时时，启动门槛、编辑、测试、commit 与 agentmemory 捕获不受阻。
5. 三种允许事件各生成一条符合 schema 和上限的摘要；普通保存和工具调用不写入。
6. 重复事件保持幂等；脏 worktree、detached HEAD、首次提交和大量变更均有确定行为。
7. 陈旧或未知索引不会产生未经验证的间接影响结论。
8. 摘要经过敏感内容扫描，索引目录不会进入 Git。

### 对照基准

选择同一仓库的 8 至 12 个任务，覆盖符号定位、影响分析、历史决策、跨模块修改和动态图谱盲区。每个任务分别以“现有工具”与“CodeGraph 轻量集成”执行，记录：

- 到达第一个正确文件的时间和工具调用数；
- 输入、输出和合计 token；
- 首次判断的正确率；
- 错误或缺失图边数量；
- 重复上下文比例；
- 最终测试结果和人工修正次数。

试点成功门槛：中位数 token 或到达正确文件时间至少改善 20%，最终正确率不下降，且错误图边未导致未经源码核验的修改。若只在大型仓库或特定语言达标，后续配置必须限制适用范围，不做全局启用。

## 试点发布与回滚

### 阶段一：仅运行时轻量集成

- 锁定 CodeGraph 版本并在一个明确仓库构建索引。
- 注册独立 MCP，加入最小路由指令。
- 事件桥保持关闭，执行对照基准。

### 阶段二：开启克制事件桥

- 只开启 commit 事件，观察重复率、摘要质量和写入噪声。
- commit 事件稳定后，再分别开启 `TaskCompleted` 和明确架构决策触发。
- 每类触发可独立配置，默认值为关闭，试点时逐项开启。

### 回滚

- 关闭宿主中的 `codegraph` MCP 配置即可回滚运行时集成。
- 关闭事件桥开关即可停止新增摘要，不删除既有记忆。
- 既有摘要保留 schema 和 provenance，可通过专用查询识别；批量删除必须单独获得用户确认。
- 回滚不修改 agentmemory 服务、控制平面、全局规范、已有 hooks 或普通项目记忆。

## 实施边界

下一阶段的实施计划只覆盖：独立 MCP 配置、最小路由指令、开关与失败回退、事件桥适配器、schema 校验、幂等和测试基准。实施前必须确认 CodeGraph 锁定版本的真实 MCP 工具名称、索引元数据能力和许可条款。

统一检索层明确不在本设计范围。只有试点数据显示 Agent 经常误路由、重复查询或无法在预算内合并两类结果，才另行提出独立设计；它不得作为本次实现的隐含依赖。
