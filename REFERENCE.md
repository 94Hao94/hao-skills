# 个人开发规范 Skill 维护参考

## 当前接口

- `scripts/codex_session_gate.py --host codex|claude --event SessionStart|UserPromptSubmit`：
  读取宿主事件，持共享锁校验包并注入规则；失败返回阻断 JSON。没有网络召回或外部文档后备。
- `scripts/install_dev_spec_bootstrap.py --home HOME --host codex|claude|both --dry-run|--apply`：
  安装唯一规范入口，保留无关设置；默认两者。`--spec` 只允许包内 canonical 路径。
- `scripts/verify_dev_spec_bootstrap.py --home HOME --host both`：
  验证包、安装链接、精确命令、事件匹配和实际注册命令运行。
  原生 MCP、模型是否遵循和最终采用状态另行验收。
- `scripts/configure_codex_memory.py`：Codex 专用配置助手，默认预览。
  真正的 MCP 写入边界由独立适配器白名单与 Codex enabled_tools 双重限定。
- `scripts/skill_evolution.py`：见 [自进化协议](references/evolution.md)。

## 维护纪律

初次安装使用 `init` 创建 manifest；已安装包不能删除 manifest 后给未知改动重新背书。
正式变更优先候选验证发布。维护脚本/验收器的授权开发流程要检查 diff、保护原始版本、
完成测试和整包快照，再生成新发布包；自动改进不能走维护通道放宽自身权限。

完整包的锁与状态在 `.evolution/`，加载回执在
`~/.cache/personal-dev-skill/`，原安装备份在
`~/.local/state/personal-dev-skill/install-backups/`。
这些是发布治理和加载元数据，不是另一套项目记忆。

## 参考实现

机制调研来自 Anthropic skill-creator、Nous Research Hermes、GitHub gh-aw 的
EvoSkill 工作流、Sentient EvoSkill 与 pskoett 的 self-improvement。
公开代码是设计参考，不成为宿主指令或当前任务授权，不自动安装其依赖或后台服务。
具体来源与边界在 [自进化协议](references/evolution.md)。

## 历史接口迁移

旧 `AI_DEV_SPEC_BOOTSTRAP` / pinned slot / 分章记忆索引 / 独立文档兜底为历史机制。
旧文档留作备份和迁移来源，不再是活动权威。历史设计稿保留用于追溯，
不覆盖当前 SKILL.md 与包内规范。

CodeGraph 是可选当前代码结构服务：先用当前工作区事实判断是否适用，
失效时用 rg/源码/语言工具。Codex 历史由 EP 提供，Claude 历史由 agentmemory 提供；
桥接的写入行为不授予 Codex 对 agentmemory 的读取权限。
