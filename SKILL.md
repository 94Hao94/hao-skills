---
name: setup-dev-spec-memory
description: 用户专属开发执行规范。开始开发、编辑、评审、测试、构建、部署、Git 操作或续接项目时必须应用；维护本 skill 的启动接入与经过验证的增量改进。普通非开发问答不展开全部章节。
---

# 个人开发执行规范

本 skill 是开发流程的规范来源，服从当前用户要求和宿主更高优先级规则。启动 hook 已加载本核心；详细规范在 [development-spec.md](references/development-spec.md)，按当前任务读取相关章节。旧独立规范文档与记忆 slot 已退出启动链路，不再读取或自动修复它们。

## 开始开发

在项目读取、修改、启动、评审、测试、构建、部署或 Git 操作前：

1. 确认 hook 注入的 skill 版本和完整性；缺失或损坏时先修复本包，不能用旧记忆或外部文档替代。
2. 核对实际 `pwd`、Git 根、worktree、分支和工作树状态。非 Git 工作区明确记录不适用。
3. 从当前配置和文档确定标准端口；启动或评审页面前核对监听进程与目标 URL，不默认 main、根仓库、第一个 worktree 或框架默认端口。
4. 再加载必要的项目记忆，与代码、测试和「说明文档.md」核对。仅有会改变结果的缺失信息、冲突或未授权高影响动作才询问；已有授权的普通工作持续完成。

## 按宿主使用记忆

- **Codex**：EP 是全部用户/项目/Agent 经验的查询入口，按需使用 `user_preference`、`user_recall`、`user_research`、`agent_recall`、`agent_research` 及来源工具。agentmemory 仅采集、写入，不做召回后备。
- EP 调用使用当前 Prompt 的 hook 所给真实 `check_id`；`memory_check` 只能复核已有绑定，不能凭空产生 ID。缺少绑定时报告入口不可用，不编造或借用其他会话 ID，也不把未绑定读取说成完整链路通过。
- **Claude**：项目记忆继续使用 agentmemory；本 skill 不迁移 Claude 到 EP。
- 项目事实、原始对话和排错日志存对应记忆系统；skill 保存通用方法，不建立第二套本地文件记忆。来源未验证、工具失败和宿主送达未知时如实报告。
- 连接修复只在首次安装或配置/服务变化后执行；正常会话不重复 connect/status/doctor。可选 CodeGraph 只提供当前代码结构，失效时用源码/rg/语言工具，不阻塞开发门槛。

## 实施与交付

- 保留无关改动；同任务复用分支，独立任务使用 `<type>/<scope>-<task>`，不加 Agent 前缀。提交/PR 为 `type(scope): description`，主干变更通过 PR。
- 保留已有集成路线。只有获针对性授权才执行破坏性历史操作、强制推送或生产发布。
- 按风险完成实现、自审和必要验证，不以声明、重复检查或工具握手代替验收；失败未修复不得报告通过。
- 维护必要项目说明；交付先说结果，再报告证据、剩余限制及是否提交/推送/合并。

## 持续改进本 skill

遵循 [evolution.md](references/evolution.md)：明确纠正可以在冲突和回归检查后固化；自行总结的经验先做候选，经旧新版对照验证后晋升。单次偶然成功、重复次数和模型自评分不是晋升证据。

每个完成任务最多自动发布一个聚焦改动，保留条件和例外、合并重复规则。发布前必须核对完整包、基线摘要、写锁、独立验收和回滚快照。权限、记忆路由、启动机制、重大流程及演进验证器不能自动放宽；不得因本 skill 而扩大当前任务授权。

## 维护入口

- 核对安装：`python3 scripts/verify_dev_spec_bootstrap.py --home "$HOME" --host both`
- 安装/修复前预览：`python3 scripts/install_dev_spec_bootstrap.py --home "$HOME" --host both --dry-run`
- 自进化命令与来源：见 [evolution.md](references/evolution.md)。宿主切换细节见 [codex-memory.md](references/codex-memory.md)。
