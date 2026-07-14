# setup-dev-spec-memory

为所有开发类对话安装确定性的通用开发规范启动契约，避免新会话只召回当前项目、选错 worktree/branch 或使用错误端口。

## 解决的问题

普通 `memory_save` 与 `memory_recall` 适合知识和历史检索，但不保证通用规范在新会话首轮稳定命中。本项目将两类职责分开：

- 控制平面：global + pinned 的 `ai_dev_spec_bootstrap`、Agent 全局指令和 Codex SessionStart gate。
- 知识平面：八章完整规范、当前索引和各业务项目记忆。
- 权威源：`~/AI开发执行规范.md`，也是 agentmemory 不可用时的降级来源。

开发任务必须先通过控制平面，再核对真实 Git 根、worktree、branch、工作树状态和标准端口，最后才召回项目记忆并开始操作。

## 前置条件

- Python 3.9 或更高版本。
- `~/AI开发执行规范.md` 包含明确的 `规范版本`。
- agentmemory 已安装并运行；安装方式以[官方仓库](https://github.com/rohitg00/agentmemory)为准。

```bash
agentmemory
# 或
npx @agentmemory/agentmemory

agentmemory connect codex --with-hooks
agentmemory status
```

## 安装

先运行测试和 dry-run：

```bash
python3 -m unittest discover -s tests -v

python3 scripts/install_dev_spec_bootstrap.py \
  --home "$HOME" \
  --spec "$HOME/AI开发执行规范.md" \
  --dry-run
```

确认路径后应用：

```bash
python3 scripts/install_dev_spec_bootstrap.py \
  --home "$HOME" \
  --spec "$HOME/AI开发执行规范.md" \
  --apply
```

安装器会：

- 将 `AGENTMEMORY_SLOTS` 和 `AGENTMEMORY_INJECT_CONTEXT` 设为 `true`；
- 合并 Codex 当前实际生效的全局 `AGENTS*.md`；
- 在 `~/.codex/hooks.json` 结构化追加独立 SessionStart gate；
- 在检测到 Claude Code 时合并 `~/.claude/CLAUDE.md`；
- 修改已有文件前创建时间戳备份；
- 保留所有非受管内容，重复运行不会重复插入。

配置变化后重启服务：

```bash
launchctl kickstart -k "gui/$(id -u)/com.agentmemory.server"
agentmemory status
```

其他平台使用对应的服务管理方式重启。

## 同步规范与 slot

1. 将权威文件按八章分别调用 `memory_save`，请求使用 `project=global-ai-dev-spec`，并检查实际返回。v0.9.27 实测可能仍保存为 `project:null`，启动流程不得依赖该字段。
2. 八章全部成功后保存索引，记录版本、SHA-256 和本轮 Memory ID。
3. 渲染契约：

   ```bash
   python3 scripts/dev_spec_contract.py render \
     --spec "$HOME/AI开发执行规范.md"
   ```

4. 使用 `memory_slot_create` 创建 `ai_dev_spec_bootstrap`：`scope=global`、`pinned=true`、`sizeLimit=4000`。已存在时验证形状后使用 `memory_slot_replace`。
5. slot 必须最后更新，避免指向只同步了一部分的规范版本。

## 验证

```bash
python3 scripts/verify_dev_spec_bootstrap.py \
  --home "$HOME" \
  --spec "$HOME/AI开发执行规范.md"
```

验证器同时检查：

- 权威版本和 SHA-256；
- 本地 flags；
- Codex/Claude 受管指令；
- Codex SessionStart hook；
- live flags；
- slot 的固定 label、global scope、pinned 状态和契约必需字段。

还应从一个与本 skill 无关的 Git 项目模拟 SessionStart，确认注入内容包含：

```bash
git rev-parse --show-toplevel
git worktree list --porcelain
git branch --show-current
git status --short --branch
```

并在启动服务或评审页面前核对项目声明的标准端口、实际监听端口和目标 URL。

## Agent 支持

| Agent | 机制 |
|-------|------|
| Codex CLI | 全局 `AGENTS*.md` + 用户 hooks + agentmemory 插件 |
| Codex Desktop | 同上，并保留 `agentmemory connect codex --with-hooks` workaround |
| Claude Code | 全局 `CLAUDE.md` + agentmemory 插件 hooks |
| 其他 MCP Agent | 使用官方 adapter；必须单独验证启动注入，不能只验证 MCP 可连接 |

## 安全边界

- 不修改 agentmemory 官方插件缓存文件。
- 不自动删除旧记忆；删除前必须列出候选并单独确认。
- 不输出 `.env` 中的密钥或 bearer secret。
- slot 和本地权威文件都不可用时，SessionStart gate 失败关闭。

详细标识、支持矩阵和故障处理见 [REFERENCE.md](REFERENCE.md)。
