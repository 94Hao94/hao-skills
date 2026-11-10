---
name: hao
description: Hao 的个人主 skill 与索引。当任务类型不明确、有多个 skill 同时适用(如 PPT、Word 文档),或不确定该用哪个 skill 时调用,按路由表选择并遵循个人偏好。开发类任务直接用 setup-dev-spec-memory,不必经过本 skill。
---

# Hao 主 skill(索引)

本 skill 只做路由:告诉你做哪类活应使用哪个 skill,以及多个 skill 都适用时的优先级。各领域的具体规范在对应的领域 skill 里,不在这里重复。

## 使用方式

1. 判断当前任务类型,在下表找到对应 skill 并调用它。
2. 表中标注"待确认"的项目,用户尚未确认优先级:出现多个候选时,先向用户说明候选并询问,不自行选择,也不把候选当作已定规则。
3. 表里没有的任务类型,按系统列出的 skill 描述正常选择,不必强行套用本表。
4. 本表只记录用户确认过的取舍;新偏好来自用户的明确纠正,经 `setup-dev-spec-memory` 的演进流程后再加入。

## 路由表

| 任务类型 | 使用的 skill | 状态 |
|---|---|---|
| 开发:读写代码、评审、测试、构建、部署、Git、环境与排错 | `setup-dev-spec-memory` | 已确认 |
| PPT / 演示文稿 | 候选:`ppt-master`、`anthropic-skills:pptx`、`lecture`(讲稿加制作说明) | 待确认 |
| Word / 文档写作 | 候选:`docs`、`anthropic-skills:docx` | 待确认 |

## 维护

- 路由表引用的 skill 名称以仓库 `skills-lock.json` 和系统已安装列表为准;发现名称失效或改名时,报告给用户,不自行改表。
- 领域规范(如 PPT、Word)等有真实任务和用户偏好素材时再新增为平级 skill,并在本表登记。
- 本 skill 保持精简,路由表不超过约 2 KB;细节放到领域 skill 的 `references/`。
