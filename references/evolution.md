# 开发规范的有证据演进

本流程只更新通用开发方法。项目事实、原始会话、排错日志继续写入 Codex 的 EP / agentmemory 写入链路或 Claude 的 agentmemory；此目录不建立另一套本地记忆。候选包、验收布尔结果、版本快照和来源定位属于发布治理资料。

## 两条路径

- 用户明确纠正：保留其条件、例外及原意，形成一个聚焦候选；冲突、适用范围和回归检查通过后发布，同一要求无需重复确认。
- Agent 总结经验：先形成候选，标明来源、问题对应的通用方法、适用范围和原因。一次成功、重复次数、模型自评都不能直接晋升；必须有实际旧版/新版对照证据。

每个任务至多自动发布一次聚焦修订。新增经验优先修正现有条款，合并重复规则。没有固定的“重复 N 次就成立”阈值；证据不足时保持 pending。

正常自动演进只允许修改 `SKILL.md` 与 `references/development-spec.md`，每次至多两个文件。验收器、测试、脚本、hook、安装器、演进规则不能通过候选修改。维护这些文件需要走另一次明确授权的开发流程。

权限扩大、记忆路由、强制启动和重大流程改变需要针对该候选的明确用户授权。工具会检查变更中的保护词及声明的保护范围，但文字检测不等同于语义审查；Agent 必须主动声明保护性改变，固定验收器的 scope 案例还应检测绕过、过度概括和冲突。不得通过换同义词、删除声明或伪造授权来绕过边界。

## 包完整性与版本

入口：`python3 scripts/skill_evolution.py --root /absolute/skill <command>`。

安装完成后运行 `init`，产生 `skill-manifest.json`。它包含 `schema_version: 1`、递增整数 `version`、`package_sha256`、全部文件摘要及修订原因。`verify_manifest(root)` 返回已校验的 manifest，校验失败抛出 `EvolutionError`；宿主 gate 必须在加载规则前调用它。

摘要覆盖全部正文、引用、脚本、测试和验收代码。仅排除 manifest 自身、`.evolution`、`.git`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache`、`.venv`、`node_modules`、`build`、`dist`、`.DS_Store` 和 Python 字节码。包内符号链接被拒绝；安装目录自身可以解析到同一个共享真实路径。`init` 不会给已被改动的已安装包重新背书。

`.evolution/` 保存锁、候选、治理策略、最小验收结果和整包快照，不进入技能正文。不要将该目录安装为独立记忆系统。已安装包的手工维护也必须在检查实际 diff 后生成新的有效 manifest；不能删除 manifest 后重建来掩盖不明变更。

## 固定验收器

候选不能自带或替换验收标准。先由用户授权的安装流程准备一个包外验收程序及固定案例，再执行：

```sh
python3 scripts/skill_evolution.py --root /absolute/skill configure-policy /absolute/policy.json
```

策略例：

```json
{
  "scope": "path-comparison",
  "approval_source_locator": "user:approved-acceptance-installation",
  "harness": ["/absolute/python3", "/absolute/acceptance.py"],
  "harness_artifacts": ["/absolute/acceptance.py", "/absolute/fixtures.json"],
  "timeout_seconds": 60,
  "cases": [
    {"id": "normalization-bug", "kind": "target"},
    {"id": "unchanged-normal-input", "kind": "normal"},
    {"id": "keep-non-path-values", "kind": "counterexample"},
    {"id": "reject-unrelated-scope", "kind": "scope"}
  ]
}
```

这只是协议示例，不是可用的开发规范验收集。实际案例必须测量可观察行为，scope 案例还须核对来源、条件、例外和规则冲突。将所有验收代码、依赖数据和 fixture 放入 `harness_artifacts`，工具固定其 SHA-256。不要以模型自评分、读取候选中的“已通过”字样或只比较标题代替验收。

程序为每个案例分别对基线与候选运行验收器，stdin 为：

```json
{"package_root": "/absolute/isolated/package", "case": {"id": "normalization-bug", "kind": "target"}, "scope": "path-comparison"}
```

验收器应只读被测包，在 stdout 只返回以下 JSON，退出码为 0：

```json
{"passed": true, "scope_matches": true}
```

目标案例中至少一项从旧版失败变成新版通过；新版必须通过全部目标、正常、反例和范围案例，两版的 scope 都必须匹配。不匹配、超时、非 JSON、非零退出或验收器摘要变化都拒绝晋升。当前实现有意不安装虚假的默认通过验收器；尚未配置可信验收时，候选保持 pending。更换策略需要显式维护安装，自动演进不能自行调整它。

## 候选、验证、发布

`stage /absolute/proposal.json` 接受：

```json
{
  "task_id": "task-unique-id",
  "kind": "user_correction",
  "focus": "只修正路径比较方法",
  "reason": "保留原输入，仅规范化路径比较值",
  "scope": "path-comparison",
  "source_locators": ["ep:source-id#user-correction"],
  "edits": {"references/development-spec.md": "完整新版规范正文"}
}
```

`kind` 也可为 `inferred_experience`。`edits` 存完整通用规则文件，不放原始对话、项目事实或日志。保护性变更另加 `"declared_protected_change": true`。stage 返回候选 ID、基线版本与摘要、新版摘要和 changed_paths。

```sh
python3 scripts/skill_evolution.py --root /absolute/skill validate CANDIDATE_ID
python3 scripts/skill_evolution.py --root /absolute/skill publish CANDIDATE_ID
python3 scripts/skill_evolution.py --root /absolute/skill status
```

`validate` 不发布；`publish` 在写锁内重新运行固定验收器，不信任现有 `validation.json`。验收结果绑定基线、新版、固定策略和每个实际输出文件的摘要。候选或基线被改动、另一个宿主先发布、同任务已经发布一次时均拒绝；重新针对当前版本 stage，不能覆盖新规则。

保护性变更需要给 validate / publish 添加 `--authorization /absolute/authorization.json`：

```json
{
  "source_locator": "user:explicit-approval-message",
  "authorized_scope": "该次明确授权的变更及边界",
  "base_sha256": "stage 返回的基线摘要",
  "candidate_sha256": "stage 返回的新版摘要",
  "changed_paths": ["references/development-spec.md"]
}
```

该记录只定位已有的人类授权，不会自行产生授权，也不允许 Agent 将自己提出的建议写成人类指令。授权旧候选不能复用于改写后的候选。脚本、测试和验收策略仍不能通过此通道修改。

## 发布故障与回滚

两个宿主使用同一真实包目录与 `.evolution/write.lock`。每次发布先创建完整包快照，逐文件原子替换，最后原子提交 manifest；过程中 gate 会因事务标记/摘要不匹配而停止加载，避免读取混合版本。发生异常立即恢复旧包。进程崩溃留下事务时，`status` 返回 `unfinished_transaction` 与待恢复快照；gate 保持阻止状态。

```sh
python3 scripts/skill_evolution.py --root /absolute/skill rollback SNAPSHOT_ID --reason '回归或中断恢复原因'
```

正常回滚恢复该快照的全部正文、引用和脚本，并产生递增的新版本，保留已发布任务记录。未完成事务的恢复只接受记录中的快照并还原其 manifest。当前包存在无关改动时拒绝正常回滚，先保护并处理实际 diff；不覆盖另一个宿主的工作。EP / agentmemory 中的新记忆不属于包快照，不会随代码回退被覆盖。

## 参考机制

- [Anthropic skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)：最小规则、实际行为验证与迭代。
- [Nous Research Hermes](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)：方法与事实分离、纠错触发局部修订。
- [GitHub EvoSkill Evolver](https://github.com/github/gh-aw/blob/main/.github/workflows/evoskill-evolver.md)：聚焦改动与保留案例检查。
- [EvoSkill](https://github.com/sentient-agi/EvoSkill)：候选提出、基线对照和回归拒绝。
- [pskoett self-improvement](https://github.com/pskoett/pskoett-ai-skills/blob/main/skills/self-improvement/SKILL.md)：区分经验记录与规则晋升；本实现不复制其本地 `.learnings` 记忆存储方式。

这些参考不意味着本实现已具备无人监督的通用技能评估能力；正式效果仍取决于固定案例质量和实际行为证据。
