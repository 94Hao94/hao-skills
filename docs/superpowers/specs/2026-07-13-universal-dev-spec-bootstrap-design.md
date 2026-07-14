# 通用开发规范启动契约设计

## 目标

让任何受支持的 AI Agent 在新的开发类对话中，先加载通用《AI 开发执行规范》，再加载当前项目记忆，并在启动、编辑、测试或 Git 操作前核对真实工作目录、worktree、branch、工作树状态和标准端口。

成功标准：

- 通用规范不依赖项目名称或模糊语义搜索才能被发现。
- 项目记忆不能替代通用规范。
- 规范加载或环境核对失败时禁止启动和修改，必须执行明确降级或报告阻塞。
- 非开发类对话只接收精简判定契约，不加载完整八章规范。
- 同步后可从任意无关 Git 项目验证同一份规范版本。

## 已确认的故障

当前系统把完整规范保存为普通 agentmemory 记忆，却假设 `memory_recall` 和 SessionStart 会稳定返回它。实际行为不满足这个假设：

- `memory_smart_search("AI开发执行规范 规范索引")` 返回低相关 session observations，没有返回规范记忆。
- `POST /agentmemory/context` 会按当前 project 构造上下文；“公司官网建设”的上下文只包含项目历史。
- `AGENTMEMORY_INJECT_CONTEXT` 已启用，但 `AGENTMEMORY_SLOTS` 未启用，slots API 返回 503。
- agentmemory 官方说明 pinned slots 才是面向 SessionStart 注入的稳定短上下文载体。
- 当前实例中存在多个未 supersede 的规范索引，`REFERENCE.md` 的静态 Memory ID 与实际状态可能漂移。
- 现有启动规则只写“调用 memory_recall”，未规定通用规范优先、返回校验、worktree/branch/port 核对及失败即停止。

## 核心原则

将系统拆成两个平面：

1. **控制平面**：始终可见的精简启动契约，负责识别开发任务、规定执行顺序和失败策略。
2. **知识平面**：完整八章通用规范与各项目历史，按需加载以控制上下文体积。

控制平面不得依赖知识平面的模糊检索结果才能生效。

## 架构

### 1. 权威源

`~/AI开发执行规范.md` 继续作为唯一权威源。版本、SHA-256 摘要校验值和章节内容都从该文件生成。禁止只修改 slot 或 agentmemory 记忆而不更新权威文件。

### 2. 全局启动契约

启用 `AGENTMEMORY_SLOTS=true`，创建或更新一个固定全局 pinned slot：

- label：`ai_dev_spec_bootstrap`
- scope：`global`
- pinned：`true`
- 内容：开发任务判定、硬性启动顺序、当前规范版本、索引标识、权威文件路径、失败降级策略。

slot 保持精简，只承担启动控制，不复制完整八章内容。各 Agent 的全局配置再保留一条最小兜底指令：新会话先遵循 `ai_dev_spec_bootstrap`；slot 不可用时读取权威文件中的启动章节。

### 3. 通用规范记忆

完整八章和索引继续写入 agentmemory，用于按需加载。所有条目请求使用统一来源标识 `project="global-ai-dev-spec"`，并验证实际返回。v0.9.27 实测接受该参数但持久化记录仍返回 `project:null`，因此该字段只能作为尽力而为的来源标识，不能参与跨项目启动正确性判断。

索引必须包含：

- 规范版本和权威文件摘要校验值；
- 八章的当前 Memory ID；
- slot label；
- 同步时间；
- 本地托底路径。

同步新版本后，旧条目需建立明确 supersede 关系；若当前 API 无法可靠自动 supersede，则先保留并在报告中列出，删除必须单独获得用户确认。

### 4. 项目记忆

完成通用规范加载后，才按当前真实 Git 根目录和 worktree 召回项目历史。项目名称、业务关键词或当前 prompt 不得用于替代通用规范加载。

### 5. 多 Agent 兜底

安装流程按 Agent 能力写入最小启动钩子：

- Claude Code：全局 `CLAUDE.md` 最小契约。
- Codex：官方插件 hooks；Codex Desktop 同时保留 `~/.codex/hooks.json` workaround。全局指令写入 Codex 实际生效的用户级文件：若 `~/.codex/AGENTS.override.md` 非空则合并到该文件，否则合并到 `~/.codex/AGENTS.md`。Codex 每个新任务都会先加载这一层。
- 其他 Agent：使用官方 adapter 支持的全局配置或启动 hook；无法自动注入的 Agent 必须在安装验证中明确标为“仅手动门槛”，不能宣称自动加载成功。

安装器必须以带起止标记的受管区块合并配置，不得覆盖用户已有内容。安装器不得修改 agentmemory 官方插件缓存中的脚本，避免升级覆盖。

### 6. Codex 强制门槛

除全局 `AGENTS.md` 行为约束外，在用户级 hooks 中增加独立的 SessionStart gate，不改 agentmemory 官方 hook：

- 安装脚本合并到 `~/.codex/hooks.json`，保留已有 hooks。
- gate 脚本从 stdin 获取真实 `cwd`，检查服务、flags、bootstrap slot 及版本。
- 检查通过时把精简契约作为 `additionalContext` 输出。
- 检查失败但权威文件可读时，输出本地降级契约并明确标记 degraded 状态。
- 服务和权威文件都不可用时返回 `continue:false` 和 `stopReason`，使 SessionStart 失败关闭。

Codex 官方当前不支持用 PreToolUse 的 `continue:false` 阻断工具调用，因此不能把它描述为机械阻断层。真正的保障由 SessionStart fail-closed、全局 `AGENTS.md` 和验证测试共同提供。若某宿主不支持可停止的 SessionStart，则支持矩阵必须如实标注为行为门槛。

## 开发任务判定

任一条件成立即进入开发启动门槛：

- 当前目录属于 Git 仓库、worktree 或明显的代码工程；
- 用户要求启动、修改、修复、评审、测试、构建、发布或部署；
- Agent 即将读取或写入项目文件、执行开发服务器、测试、构建或 Git 命令。

普通问答可以跳过完整规范和项目核对，但全局启动契约仍需可见，以便 Agent 在行为转为开发操作前重新判定。

## 硬性启动门槛

开发类任务必须按顺序执行：

1. 读取 `ai_dev_spec_bootstrap`，校验 label、版本和权威文件路径。
2. 加载通用规范索引及与当前任务相关的章节；不得把“未命中”解释为“没有通用规范”。
3. 若 slot、索引或版本校验失败，立即读取 `~/AI开发执行规范.md`；仍失败则停止开发操作并报告阻塞。
4. 获取并报告以下环境事实：
   - 当前 `pwd`；
   - `git rev-parse --show-toplevel`；
   - `git worktree list --porcelain`；
   - 当前 branch；
   - `git status --short --branch`；
   - 项目文档、脚本和配置声明的标准启动端口；
   - 当前监听端口与目标页面 URL（需要启动或评审页面时）。
5. 识别真正的目标 worktree。根仓库、`main` 或默认端口都不得作为未经核验的默认值。
6. 召回与该项目、worktree 和 branch 相关的项目记忆。
7. 对照项目说明文档、实际状态和用户目标；存在冲突时先报告并确认。
8. 只有以上检查通过后，才允许启动服务、编辑文件、运行会改变状态的命令或开始页面评审。

## 同步流程

同步必须近似事务化：

1. 检查 agentmemory 服务、当前 Agent 接入、hooks 和功能 flags。
2. 读取权威规范，解析版本、章节并计算摘要校验值。
3. 写入或更新八章记忆。
4. 写入新索引，索引引用本轮八章 ID。
5. 启用并验证 slots。
6. 最后更新 `ai_dev_spec_bootstrap`，使其指向已完整写入的新版本。
7. 更新各 Agent 的最小兜底配置。
8. 重启服务或 Agent（仅在配置变更确实需要时）。
9. 从与规范无关的测试项目执行端到端验证。
10. 验证全部通过后创建 agentmemory snapshot，并更新仓库内的生成记录。

如果第 3 至 6 步失败，旧 bootstrap 必须保持有效，不能指向半完成版本。

## 仓库改造

### `SKILL.md`

- 将触发条件扩展为首次安装、规范更新、启动契约丢失、跨项目未召回、hooks/slots 失效。
- 用“控制平面 + 知识平面”替换现有三层架构描述。
- 增加固定 slot、开发任务判定、硬门槛、原子同步和失败降级流程。
- 删除“只要执行 memory_recall 即可”的隐含假设。

### `REFERENCE.md`

- 不再把手工维护的 Memory ID 表作为长期真相。
- 记录稳定 label、project 标识、验证查询、版本校验和故障矩阵。
- Memory ID 由同步结果生成或在验证报告中记录。

### `README.md`

- 说明自动加载与普通搜索的区别。
- 给出各 Agent 的支持级别和安装后验证方法。
- 明确 `AGENTMEMORY_SLOTS` 与 `AGENTMEMORY_INJECT_CONTEXT` 都是自动启动契约所需条件。

### 自动化脚本

增加可重复执行的验证脚本，至少检查：

- SKILL frontmatter 和引用文件有效；
- 权威规范版本可解析；
- bootstrap 内容包含所有硬门槛字段；
- slots 和 injection flags 已启用；
- slot 是 global + pinned；
- 无关项目 context 能获得 bootstrap；
- 模糊搜索失败时会进入本地文件降级；
- worktree、branch 和 port 检查命令存在且顺序正确。

脚本不得输出环境变量密钥或完整敏感配置。

安装脚本对用户级配置采用备份、结构化合并和幂等更新；不得重写整个 `hooks.json`、`AGENTS.md`、`CLAUDE.md` 或 `.env`。

## 错误处理

| 故障 | 行为 |
|------|------|
| agentmemory 不可达 | 读取权威文件；禁止宣称已完成记忆召回 |
| slots 未启用或返回 503 | 标记自动启动契约未安装，启用后重启并复验 |
| bootstrap 缺失或版本错误 | 不执行项目操作，回退权威文件并修复同步 |
| 索引搜索返回低相关记录 | 不继续尝试项目关键词；使用 bootstrap 中的确定性标识或权威文件 |
| Agent hooks 未派发 | 使用 Agent 全局配置兜底，并在支持矩阵中记录限制 |
| worktree/branch 不一致 | 停止启动或编辑，报告候选 worktree 并确认目标 |
| 端口来源冲突 | 列出文档、配置和实际监听值，确认后再启动或评审 |
| 旧记忆重复 | 列出 supersede/删除候选；删除前单独确认 |

## 验证场景

1. 在一个与本 skill 无关的新 Git 仓库开始“启动项目”，必须先显示通用规范版本和环境核对结果。
2. 根仓库位于 `main`，功能 worktree 位于其他 branch，项目记忆声明功能 worktree；必须选择功能 worktree，不能默认根目录。
3. 项目文档声明端口 4328，配置中另有历史端口 4327；必须报告冲突并以当前权威项目状态核对。
4. 精确规范搜索返回无关 observation；系统必须走确定性 bootstrap/本地托底，而不是宣布规范不存在。
5. agentmemory 服务停止；系统必须读取本地规范并禁止无提示继续。
6. slots 被禁用；安装验证必须失败，不能只因 `memory_recall` 可调用就通过。
7. 普通非开发问答不加载完整八章；一旦请求变为修改代码，立即进入开发门槛。
8. Claude Code、Codex CLI 和 Codex Desktop 分别验证；其他已安装 adapter 至少执行配置静态检查。

## 不在本次范围

- 修改 agentmemory 官方服务或插件源代码。
- 自动删除现有旧记忆。
- 为每个业务项目写入相同的完整通用规范副本。
- 把完整规范永久注入每次非开发对话。

## 交付物

- 更新后的权威《AI 开发执行规范》启动章节。
- 更新后的 `SKILL.md`、`REFERENCE.md` 和 `README.md`。
- bootstrap 模板及安全的安装/验证脚本。
- 各 Agent 最小启动钩子模板或生成逻辑。
- 测试记录，包括 RED 基线、修复后验证和仍受宿主限制的项目。
