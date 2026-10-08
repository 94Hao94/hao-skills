# 个人开发执行规范 Skill

本项目沿用 `setup-dev-spec-memory` 标识，提供 Codex 与 Claude 共用的开发执行规范、
强制启动加载和有证据的增量更新。规范唯一来源是
[references/development-spec.md](references/development-spec.md)；旧独立文档与
agentmemory slot 不再参与启动。

## 宿主边界

| 宿主 | 规范 | 项目与经验召回 | agentmemory 写入 |
| --- | --- | --- | --- |
| Codex | 本 skill | EP | 独立采集与显式写入 |
| Claude | 同一份 skill | agentmemory | 保持既有插件与配置 |

安装不会修改共享 `~/.agentmemory/.env`、全局注入开关、数据库或 Claude 的记忆路由。
其他已安装 hooks 与用户指令保持合并。启动 hook 注入核心规则；详细八章按任务读取。
安装源摘要覆盖正文、引用、脚本与测试；缺失或损坏时返回阻断响应，不回退旧文件。

## 安装与核验

需要 Python 3.10+。先把完整包放在持久目录，不要使用会被清理的临时目录或插件缓存。
在包内完成验证后初始化 manifest：

```sh
python3 scripts/skill_evolution.py --root . init
python3 scripts/install_dev_spec_bootstrap.py --host both --dry-run
python3 scripts/install_dev_spec_bootstrap.py --host both --apply
python3 scripts/verify_dev_spec_bootstrap.py --host both
```

两个宿主安装链接指向同一个真实包。原安装链接或目录移入宿主技能发现范围外的
私有备份目录，避免旧技能被重复发现。配置变更先做完整预览，再持锁核对并原子应用；
失败恢复已完成的修改。符号链接形式的宿主配置须先明确实际修改目标，不能静默拆掉链接。

SessionStart 覆盖新建、恢复、清空和压缩恢复；UserPromptSubmit 校验包摘要，仅在版本变化
时重新加载。每个会话的加载回执只有宿主、包版本和摘要，不保存任务或对话。
宿主必须支持且信任 hooks；静态配置与命令测试不能替代原生宿主送达验证。

Codex 的 EP MCP 与 agentmemory 写入切换见
[references/codex-memory.md](references/codex-memory.md)。只有 EP 真实读取通过后，
才能称召回链路可用；仅有 tools/list 或健康端点不是完整验收。

## 自进化

详细协议见 [references/evolution.md](references/evolution.md)：

- 明确纠正按原意和适用条件修订；推断经验先做候选。
- 发布重新运行固定的旧版/新版行为验收，不接受候选自带的分数或“已通过”声明。
- 正常、反例和范围案例不得回归；无目标改善证据保持 pending。
- 每任务至多自动发布一个聚焦修订；共享锁、基线校验和完整包快照防止并发覆盖。
- 保护性边界需要针对性用户授权；自动候选不能改验证器、脚本或验收标准。

本包提供发布机制与必要检查，不内置虚假的通用“语义改进”验收器。
针对实际改进先冻结独立目标与保留案例，再配置包外验收策略。没有这类证据时保留候选，
不能把结构合法或模型自评说成效果改善。用户已明确纠正的同一要求不重复请求许可。

## 开发验证

```sh
XDG_CACHE_HOME=/tmp/personal-dev-skill-tests python3 -m unittest discover -s tests -v
git diff --check
```

可选 CodeGraph 的现有安装、桥接与测试保留；它提供当前代码结构，不替代宿主记忆路由。
任何当前项目、worktree 或 branch 的历史信息仍须核对实际源码和作用域。

## 统一 Git 规范

新任务分支使用 `<type>/<scope>-<task>`，不加 Agent 前缀。类型、Agent 工作流、本地 hook
安装、迁移兼容名单和 GitHub 必要检查见 [Git 规范](docs/git-policy.md)；权威规则为
[development-spec.md](references/development-spec.md) 第六章。
