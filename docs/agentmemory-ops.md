# AgentMemory 运维说明

> 第 1–3 节描述 v2.0 之前的 slot 启动方式，仅作历史参考；现行启动与规范以 skill 内 `SKILL.md` 和 `references/development-spec.md` 为准。第 4 节起的故障分析、补丁与回滚步骤仍适用于 AgentMemory v0.9.28。

## 1. 项目用途

本项目把 `~/AI开发执行规范.md` 安装为跨 Agent、跨项目稳定生效的开发启动契约。它将“每次开发前必须执行的控制规则”与“按项目检索的历史知识”分开，避免普通记忆搜索排序变化导致规范漏载。

## 2. 组成与数据流

主要组件如下：

- `scripts/dev_spec_contract.py`：解析权威规范并渲染短启动契约。
- `scripts/install_dev_spec_bootstrap.py`：合并受管区块、配置 slot 和 SessionStart gate；保留用户已有内容。
- `scripts/codex_session_gate.py`：启动时读取 global pinned slot，失败时回退到本地权威文件并失败关闭。
- `scripts/verify_dev_spec_bootstrap.py`：核对权威文件、配置、hooks、在线 flags 和 slot。
- `scripts/agentmemory-watchdog.sh`：验证 3111 监听者归属，安全地恢复 AgentMemory worker 与 iii 引擎。
- `scripts/patch_agentmemory_viewer.py`：幂等地将 v0.9.28 Viewer 首页改为轻量、不可重入刷新。
- `scripts/patch_agentmemory_health.py`：让 v0.9.28 按真实 V8 heap limit 评估和显示内存压力。
- `scripts/patch_agentmemory_sessions_summary.py`：为首页增加有界 sessions 摘要响应，跳过逐条 summary 读取。
- `scripts/patch_agentmemory_dashboard_summary.py`：为 semantic、procedural、relations 提供缓存总数和有界预览，并用 state event 增量保持准确。

开发会话的顺序是：加载通用契约 → 验证真实工作区/Git/worktree/branch/端口 → 召回项目记忆 → 执行开发任务。

连接检查是一次性配置动作，不是会话动作：首次安装或 AgentMemory 的适配器、配置、服务发生变化时才运行 `agentmemory connect <agent>`、`agentmemory status` 或 `agentmemory doctor`。正常 SessionStart 复用已安装的 hooks、MCP 和 global pinned slot。

## 3. 关键目录

- 权威规范：`~/AI开发执行规范.md`
- AgentMemory 配置：`~/.agentmemory/.env`
- AgentMemory 运行数据：`~/data/state_store.db`、`~/data/stream_store`
- AgentMemory 日志：`~/.agentmemory/logs`
- LaunchAgents：`~/Library/LaunchAgents/com.agentmemory.server.plist`、`com.agentmemory.watchdog.plist`
- 本项目测试：`tests/`

配置文件可能包含密钥。诊断时只查询所需键名或布尔值，不得输出完整 `.env`，也不得把密钥写入日志、文档或 AgentMemory。

## 4. 2026-07-31 CPU 与空实例故障

### 4.1 表现

- AgentMemory CPU 一度约 163%，后续 Node 与 iii 各约 170–200%。
- REST 的 `/agentmemory/*` 路径一度返回 404，CLI 因此显示一个空实例。
- 磁盘数据实际存在：恢复前 `state_store.db` 有 369 个文件，`stream_store` 有 294 个文件，并非记忆被删除。

### 4.2 根因

第一层是 watchdog 所有权判断过严。进程命令使用 `~/data/iii-config.yaml`，watchdog 只接受 `~/.agentmemory/iii-config.yaml` 的字面路径；两者内容相同，但 watchdog 连续拒绝恢复，造成 iii 存活而 AgentMemory API 未注册的“空实例”。

第二层是 Viewer 负载放大。v0.9.28 仪表盘启动时通过 `Promise.all` 并发查询 health、sessions、memories、graph、audit、semantic、procedural、relations、lessons 和 crystals。对当前大状态库，Node 栈集中在 V8 JSON parse/stringify、UTF-8 编码和 GC；iii 栈集中在 serde_json、BTreeMap 克隆/插入和内存复制。Viewer 被隔离后，同一实例 CPU 立即从约 170–200% 降到 0.0–0.1%，证明它是直接触发器。

第三层是内存误报与 sessions 放大。v0.9.28 用 `heapUsed / heapTotal` 计算内存压力，而 `heapTotal` 只是已提交堆，不是进程可用上限；当 RSS 超过固定 512 MB 时，正常 GC 扩堆也会显示 `memory_critical_96%_rss...`。同时 `/sessions` 忽略 limit 参数并为 342 个 session 逐条读取 summary，单次响应约 591 KB，造成额外峰值。当前 LaunchAgent 的 V8 heap limit 实测约 6240 MB。

历史自动压缩、图提取和汇总任务还受到上游 LLM 503/524/超时影响，会进一步放大负载，但在本次空闲高 CPU 中不是持续热点。

### 4.3 已实施修复

- 先将数据、环境配置、watchdog 和 LaunchAgent 配置备份到 `~/.agentmemory/backups/cpu-empty-repair-20260731-232412`。
- watchdog 现在允许批准的 iii 二进制使用字节完全一致的迁移配置，并保留外来进程保护。
- 保持 `CONSOLIDATION_ENABLED=true`、`GRAPH_EXTRACTION_ENABLED=true` 和 `AGENTMEMORY_AUTO_COMPRESS=true`；使用 `AGENTMEMORY_AUTO_COMPRESS_EVERY_N=10`、`CONSOLIDATION_INTERVAL_MS=21600000`、`SUMMARIZE_CHUNK_CONCURRENCY=1` 限制调用频率和并发。`AGENTMEMORY_INJECT_CONTEXT=false` 关闭每次工具调用的自动上下文拼接，性能和质量修复不依赖删除历史数据。
- 为本机 AgentMemory v0.9.28 运行包增加 `AGENTMEMORY_VIEWER_ENABLED` 支持，并把首页从 10 路并发改为 health、sessions、memories、audit 四路请求；刷新尚未完成时不再启动第二轮。
- 健康快照新增 `heapLimit`，内存告警和 Viewer 堆仪表改用 `heapUsed / heapLimit`，旧服务仍回退到 `heapTotal`。
- `/sessions?summary=true` 只返回聚合统计与最近 5 条，不读取 342 个 summary；Viewer 首页改用该接口。
- 重新设置 `AGENTMEMORY_VIEWER_ENABLED=true`，恢复 3113 Viewer；REST、MCP、hooks 和记忆数据不受影响。
- 按 `bootout server → 停止 iii → 部署 → bootstrap server` 顺序重启，避免 LaunchAgent KeepAlive 在部署期间重新加载旧代码。

### 4.4 验证证据

修复后：

- `agentmemory status`：healthy，342 sessions，约 9861 observations，104 effective memories。
- REST 查询：112 条 memories（含 orphan/latest 口径差异），数据目录为 370/295 个 state/stream 文件。
- `livez`：`viewerPort=3113`、`viewerSkipped=false`，Viewer 根页面返回 HTTP 200。
- 每 5 秒模拟一次四路首页请求的高强度测试中，瞬时峰值为 Node 48.8%、iii 25.7%；停止请求后的 30 秒六次采样为 Node 0.0–0.2%、iii 0.0%，未出现持续高占用。
- sessions 首页响应由 591459 字节降为 2458 字节；仍返回 342 total、7 active、9861 observations 和最近 5 条。
- 两个刷新周期内状态持续 healthy，heap limit 为 6240 MB；RSS 首周期峰值 667 MB，随后回落到 569/556 MB，CPU 约 1%，未观察到周期累加。剩余常驻主要与 `EMBEDDING_PROVIDER=local` 的本地模型/原生运行时有关。
- watchdog LaunchAgent 最新退出码为 0。

### 4.6 2026-09 启动重复与主动上下文质量

后续检查发现两类独立问题：启动契约没有明确连接检查的“一次性”边界，模型因此在每个 SessionStart 重复执行 `connect`/`status`/`doctor`；同时官方插件的自动 context/enrich 注入会把未按当前项目过滤的 pinned slot 与低质量历史上下文加入每次工具调用。当前修复将 `AGENTMEMORY_INJECT_CONTEXT=false`，保留官方 SessionStart/生命周期 hooks 的会话捕获，并在契约、安装器、验证器和现行文档中明确正常会话不重复连接检查。没有删除历史记忆、slot 或数据库数据。

重新开启全部功能后的补充验证：

- 自动压缩按每 10 条观察触发，现场连续触发 3 次，其中 2 次成功写回结构化记录；另 1 次正确记录上游模型 503，circuit breaker 保持 closed。
- 单条真实压缩记录的图谱提取成功，产生 4 个节点和 3 条关系；未运行全库 graph build。
- semantic consolidation 成功处理 256 份已有摘要并写入 21 条新事实。
- 上述调用结束后连续 30 秒采样，AgentMemory Node 进程 CPU 为 0.0–0.3%，iii 为 0.0%；健康快照 CPU 为 1.32%、event-loop lag 为 0.12 ms、alerts 为空。Node RSS 约 486 MB，heap used/limit 约 163/6240 MB。
- Viewer 根页面和健康接口均返回 HTTP 200，响应时间低于 10 ms。

### 4.5 2026-08-02 Viewer 合并状态错误显示 0

轻量首页补丁最初为了移除高基数集合请求，把 `state.dashboard.semantic`、`procedural` 和 `relations` 直接设为空数组；渲染器又用这些数组的长度显示 consolidation 状态，导致真实数据存在时仍显示 0。这是展示层回归，不是记忆删除或数据目录错位。

修复后新增 `/agentmemory/dashboard/collections-summary`：首次读取集合建立进程内缓存，后续由 iii state event 对 create/update/delete 增量更新；每次首页刷新只返回准确总数以及 semantic/procedural 各 5 条、relations 最多 8 条预览。`graph/stats` 使用已有 snapshot 统计，未恢复高成本全库扫描。

现场验证中，摘要接口与原始接口均返回 semantic 3135、procedural 14、relations 0，摘要响应约 21 KB。将完整首页刷新加速到每秒一次并连续执行 15 次时，瞬时峰值为 Node 38.5%、iii 34.1%；停止请求后约 4 秒回落到 Node 0.1%、iii 0.0%，未出现持续高 CPU。部署前文件备份位于 `~/.agentmemory/backups/viewer-zero-count-fix-20260802-012733`。

## 5. 运维与回滚

常规验证：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_dev_spec_bootstrap.py \
  --home "$HOME" \
  --spec "$HOME/AI开发执行规范.md"
curl -fsS http://127.0.0.1:3111/agentmemory/livez
agentmemory status
```

轻量首页和后续指标补丁的备份位于上述目录，后缀包括 `before-light-dashboard`、`before-heap-limit-health` 和 `before-session-summary`。若需回滚，先卸载 `com.agentmemory.server`，恢复同一阶段的两个运行模块和 Viewer 文件，再重新 bootstrap 服务。不要混用不同阶段的备份。

AgentMemory 升级可能覆盖全局运行包补丁。升级后先用四个 `patch_agentmemory_*.py --check` 工具验证布局；若仍兼容则备份后应用，否则停止并重新分析新版本，不要直接打开旧式仪表盘。

## 6. 修改约束

- 开发规范只在 skill 内 `references/development-spec.md` 维护，不再使用 `~/AI开发执行规范.md` 或记忆 slot。
- 不删除旧记忆或状态目录来处理“空实例”。
- 修改运行配置或全局包前必须备份；验证失败时停止并回滚，不重复强制重启。
- 不在仓库、命令输出或长期记忆中保存 API key、token 或完整 `.env`。
