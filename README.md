# hao-skills

Hao 的个人 skill 套件:一个主 skill 加若干领域 skill,Codex 与 Claude 共用同一份内容、
同一套完整性校验和有证据的增量更新。

设计取向类似 superpowers:一个品牌式主 skill `hao` 负责路由,各领域规范是平级的独立 skill,
需要时才加载,互不干扰。

## 包含的 skill

| skill | 作用 | 状态 |
| --- | --- | --- |
| `hao` | 主 skill 与索引:任务类型不明确或多个 skill 适用时,按路由表选择并遵循个人偏好 | 已上线,PPT/Word 的优先级待确认 |
| `setup-dev-spec-memory` | 开发执行规范:开发启动门槛、编码与质量、Git 规范、问题处理与汇报 | 已上线 |
| PPT、Word 等领域规范 | 有真实任务和偏好素材时新增为平级 skill | 计划中 |

结构设计与取舍见 [个人 skill 套件设计](docs/superpowers/specs/2026-11-10-personal-skill-suite-design.md)。

## 目录结构

```text
SKILL.md                     setup-dev-spec-memory 的入口(包根即该 skill)
references/                  开发规范 v2.0、自进化协议、Codex 记忆切换
skills/hao/                  主 skill
scripts/                     安装、校验、启动 gate、自进化、CodeGraph 集成、AgentMemory 运维
tests/                       全部脚本的测试
docs/                        设计、计划、Git 规范、AgentMemory 运维说明
```

## 命名约定

- 项目(仓库)名为 `hao-skills`。
- 开发 skill 的名字仍是 `setup-dev-spec-memory`,**不随项目改名**:宿主 hook、
  `~/.claude/skills` 与 `~/.codex/skills` 下的链接、`CLAUDE.md`/`AGENTS.md` 引导块都依赖它。
- agentmemory 的项目标识 `94Hao94/setup-dev-spec-memory`(见 `codegraph-agentmemory.json`)
  保持不变,改了会使既有历史记忆与新项目断开。

## 宿主边界

| 宿主 | 规范 | 项目与经验召回 | agentmemory 写入 |
| --- | --- | --- | --- |
| Codex | 本套件 | EP | 独立采集与显式写入 |
| Claude | 同一份套件 | agentmemory | 保持既有插件与配置 |

安装不会修改共享 `~/.agentmemory/.env`、全局注入开关、数据库或 Claude 的记忆路由。
其他已安装 hooks 与用户指令保持合并。启动 hook 注入开发核心规则;详细章节按任务读取。
安装源摘要覆盖正文、引用、脚本与测试;缺失或损坏时返回阻断响应,不回退旧文件。

## 安装与核验

需要 Python 3.10+。先把完整包放在持久目录,不要使用会被清理的临时目录或插件缓存。
在包内完成验证后初始化 manifest:

```sh
python3 scripts/skill_evolution.py --root . init
python3 scripts/install_dev_spec_bootstrap.py --host both --dry-run
python3 scripts/install_dev_spec_bootstrap.py --host both --apply
python3 scripts/verify_dev_spec_bootstrap.py --host both
```

两个宿主的安装链接指向同一个真实包:`setup-dev-spec-memory` 链接到包根,
包内每个 `skills/<名字>/` 各自链接到宿主的 skill 目录(如 `hao`)。
原安装链接或目录移入宿主技能发现范围外的私有备份目录,避免旧技能被重复发现。
配置变更先做完整预览,再持锁核对并原子应用;失败恢复已完成的修改。
符号链接形式的宿主配置须先明确实际修改目标,不能静默拆掉链接。

SessionStart 覆盖新建、恢复、清空和压缩恢复;UserPromptSubmit 校验包摘要,仅在版本变化
时重新加载。每个会话的加载回执只有宿主、包版本和摘要,不保存任务或对话。
宿主必须支持且信任 hooks;静态配置与命令测试不能替代原生宿主送达验证。

Codex 的 EP MCP 与 agentmemory 写入切换见
[references/codex-memory.md](references/codex-memory.md)。只有 EP 真实读取通过后,
才能称召回链路可用;仅有 tools/list 或健康端点不是完整验收。

同步已安装包时,必须从干净的 `git archive` 导出比对,不要直接用含本地忽略文件的工作目录
(`data/`、`artifacts/`、`.codegraph/`、`.agents/` 不属于包,但包的文件清单不读 `.gitignore`)。

## 自进化

详细协议见 [references/evolution.md](references/evolution.md):

- 明确纠正按原意和适用条件修订;推断经验先做候选。
- 发布重新运行固定的旧版/新版行为验收,不接受候选自带的分数或"已通过"声明。
- 正常、反例和范围案例不得回归;无目标改善证据保持 pending。
- 每任务至多自动发布一个聚焦修订;共享锁、基线校验和完整包快照防止并发覆盖。
- 保护性边界需要针对性用户授权;自动候选不能改验证器、脚本或验收标准。

本包提供发布机制与必要检查,不内置虚假的通用"语义改进"验收器。
针对实际改进先冻结独立目标与保留案例,再配置包外验收策略。没有这类证据时保留候选,
不能把结构合法或模型自评说成效果改善。用户已明确纠正的同一要求不重复请求许可。

## 统一 Git 规范

新任务分支使用 `<type>/<scope>-<task>`,不加 Agent 前缀。类型、Agent 工作流、本地 hook
安装、迁移兼容名单和 GitHub 必要检查见 [Git 规范](docs/git-policy.md);权威规则为
[development-spec.md](references/development-spec.md) 第六章。

## 可选组件

- **CodeGraph**:提供当前代码结构,不替代宿主记忆路由。任何当前项目、worktree 或 branch 的
  历史信息仍须核对实际源码和作用域。
- **AgentMemory 运维**:watchdog 与 v0.9.28 补丁,见 [docs/agentmemory-ops.md](docs/agentmemory-ops.md)。

## 开发验证

```sh
XDG_CACHE_HOME=/tmp/personal-dev-skill-tests python3 -m unittest discover -s tests -v
git diff --check
```

`tests/test_agentmemory_watchdog.py` 中的计时用例有 5 秒超时,机器负载高时可能偶发失败,
单独重跑可通过。

## 文档导航

- [维护参考](REFERENCE.md)
- [开发规范 v2.0](references/development-spec.md)
- [自进化协议](references/evolution.md)
- [个人 skill 套件设计](docs/superpowers/specs/2026-11-10-personal-skill-suite-design.md)
- [Git 规范](docs/git-policy.md)
- [AgentMemory 运维](docs/agentmemory-ops.md)
