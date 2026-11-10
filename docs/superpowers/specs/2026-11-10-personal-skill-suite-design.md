# 个人 skill 套件:结构设计

状态:设计稿,待用户审阅。本文只定结构与迁移路径,不含实现。

## 1. 目标

把现在只管开发规范的 `setup-dev-spec-memory`,扩展为个人偏好套件:

- 以后可以按领域增加规范,如 PPT、Word 文档,各自独立演进。
- 有一个统一入口,说明"做什么类型的活应使用哪个 skill",并记录个人偏好。
- Claude 与 Codex 共用同一份内容与同一套完整性校验。

非目标:

- 不引入新的记忆系统,项目事实仍存宿主记忆(规范 1.4 节)。
- 不在没有真实偏好素材时预先编写 PPT/Word 规范。
- 不改变现有开发 skill 的名称、路径、hook 与规则正文。

## 2. 已核实的约束

来源:官方文档 `https://code.claude.com/docs/en/skills`(约 1.9 万字符未读到)。

- skill 只识别 `<skills 目录>/<名字>/SKILL.md` 一层,文档未记录 `skills/<分组>/<名字>/` 的分组写法。因此不能靠目录嵌套做"大 skill 含子 skill"。
- 所有 skill 的 description 常驻上下文,正文调用时才加载;description 与 `when_to_use` 合计在列表中超过 1536 字符会被截断。
- 官方建议 SKILL.md 不超过 500 行,细节放同目录引用文件。
- 可用 `paths`、`disable-model-invocation`、`user-invocable` 控制触发。

## 3. 目标结构

采用"一个索引 skill 加若干平级领域 skill":

```
skills/
  hao/                      主 skill(索引):路由表与跨领域偏好
  setup-dev-spec-memory/    开发规范,名称与路径不变
  ppt-spec/                 以后按需新增
  word-spec/                以后按需新增
```

不采用的方案:

- 单个 skill 加 `references/`:一个 description 要覆盖全部领域,触发过粗,开发门槛核心会混入无关任务。
- 只有平级 skill、没有索引:没有"什么活用哪个 skill"的统一入口,也无法表达第三方 skill 之间的优先级。

### 3.1 索引 skill

- 名称 `hao`(已确认),类似 superpowers 的品牌式主 skill 名;斜杠命令为 `/hao`。目录 `skills/hao/`。
- description 保持很窄,只在"拿不准用哪个 skill"或"多个 skill 都适用"时触发,避免像开发 skill 那样对几乎所有任务触发。
- SKILL.md 只放路由表与跨领域偏好,目标不超过约 2 KB;细节放 `references/`。
- 路由表记录两类内容:
  1. 任务类型到 skill 的映射,含第三方 skill(如 `ppt-master`、`pptx`、`docs`、`docx`、`lecture`)。
  2. 多个 skill 同时适用时的优先级与理由。
- 路由表的具体取舍(例如 PPT 优先 `ppt-master` 还是 `pptx`)**必须由用户确认**后写入,不由 Agent 自行推断。
- 路由表引用的第三方 skill 名称以 `skills-lock.json` 为参照,校验时报告失效或改名的引用。

### 3.2 领域 skill

- 每个领域独立 description,只描述自己的触发条件,各自不超过 1536 字符。
- 内容来自用户的真实纠正和满意成品,按 `references/evolution.md` 的"明确纠正"路径沉淀;没有素材时不创建。
- 开发 skill 的核心规则、章节和 hook 注入保持现状。

## 4. 安装与完整性

- 仓库根目录保持为发布包根;各 skill 位于 `skills/<名字>/`。
- 安装器把每个 skill 子目录分别链接到 Claude 与 Codex 的 skill 目录,原有 `setup-dev-spec-memory` 链接的名称不变。
- 现状是包根目录本身被链接为一个 skill,迁移时需要先把 `SKILL.md`、`references/` 移入 `skills/setup-dev-spec-memory/`,并同步 manifest、gate 与校验器;这一步属于独立的、需明确授权的维护。
- 完整性仍用单个包级 manifest 覆盖所有 skill;自动演进的可修改路径白名单后续按 skill 扩展,验证器与白名单的修改需单独授权。
- 常驻注入预算:hook 只注入开发核心与索引的极简摘要,总量不超过现状的两倍;各领域规范按任务读取。

## 5. 分步路径

1. **本文**:确认结构与命名。
2. **加主 skill `hao`**:新增 `skills/hao/` 与路由表草稿,开发 skill 不动;安装器多链接一个目录;路由取舍由用户确认。
3. **按需加领域 skill**:有真实 PPT/Word 任务和偏好素材时再建。
4. **泛化演进与校验**:出现两个以上领域 skill 后,再调整 manifest 白名单、gate 与校验器,并单独取得授权。

每一步独立成分支和 PR,完成后同步已安装包并重新校验。

## 6. 风险

| 风险 | 对策 |
|---|---|
| 改名导致 hook、链接、引导块失效 | 开发 skill 名称与路径不变,只新增兄弟目录 |
| 索引过度触发造成上下文膨胀 | description 保持很窄,路由表限制篇幅 |
| 路由表引用的第三方 skill 被删或改名 | 以 `skills-lock.json` 校验,报告失效项 |
| 未经验证的偏好被固化为规则 | 偏好须由用户确认或来自真实纠正,不由 Agent 推断 |
| 多 skill 安装破坏现有完整性校验 | 第 4 步前不改校验器;迁移前先备份已安装包并保留快照 |

## 7. 待用户确认

- ~~索引 skill 名称~~:已确认为 `hao`。
- 第 2 步路由表中第三方 skill 的优先级取舍。
- PPT、Word 是否已有可作为素材的过去纠正或成品。
