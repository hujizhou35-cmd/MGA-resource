# 当前候选包的云端恢复验收

目录 `assembly-42289434868-1` 的候选包已在云端完成真实构建、独立下载恢复和范围核验。精选现已扩展到60个不同家族、十二类各5件，并完成实际显示和用途组合审阅。整体素材验收与网站发布仍未通过。

| 实际结果 | 数量／状态 |
|---|---|
| 选中原件 | 19,473 |
| 去重后的原件与预览对象 | 38,748 |
| 干净分包 | 13个，合计3,342,746,415字节 |
| 已排除／待定内容混入 | 0 |
| Commons对照 | 01–04保留，05–06排除 |
| 范围验收失败／待提供证据 | 0／0 |
| 实际显示审阅 | 60件、30张检查页、420个显示条件 |
| 用途组合审阅 | 12张画布、62处主图摆放，覆盖全部60件 |
| 恢复所用代码的CI | 526项通过（提交 `4ca726b`） |
| 最新元数据重放代码CI | 577项通过（提交 `a28b9ba`，运行 `42323090243`） |

[构建任务](https://github.com/hujizhou35-cmd/MGA-resource/actions/runs/42309798276)和[独立恢复任务](https://github.com/hujizhou35-cmd/MGA-resource/actions/runs/42312769493)是两次不同的运行。恢复任务确实重新读取远端索引、manifest和13个分包，核对完整压缩流、精确成员、大小及SHA-256；没有拿构建自检代替。原件只在云端处理，本机接收元数据和有界小预览。

可核对的资料：

- [QA候选归档](https://github.com/hujizhou35-cmd/MGA-resource/releases/tag/publish-qa-20261107-42289434868-3.4.3)，明确标为非正式发布。
- [独立恢复回执](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/qa-20261107-42289434868-3.4.3-42312769493-1.restore_evidence.json)。
- [48件精选预览与检查页](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/restored-curated-42312769493-1.tar.gz)，约8.08 MB，不含图库原件。
- [范围审计与逐件显示审阅元数据](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/qa-acceptance-42312769493.metadata.tar.gz)，约0.64 MB，不含图像。
- [此前证据关联阶段快照](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/acceptance-evidence-42312769493.metadata.tar.gz)，419,796字节、7份元数据文件，当时尚有100条正式待确认；远端大小和SHA-256均已核对，摘要为 `ef000ba001925cf5124b72cf90a45f281559673501f37d16ff358890263ad3e7`。
- [此前98条逐题确认快照](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/formal-query-confirmation-42312769493.metadata.tar.gz)，576,829字节、10份元数据文件；摘要为 `324e0333cac85c4dd237f658d4eaf74f0a7b2d6b9d74a182b96b26aaa06c1dda`。其中两条内皮部件判断已按原验收规则纠正，旧快照保留。
- [全部版本与哈希记录](../data/diagnostics/cloud-qa-recovery-42312769493.json)。
- [60件精选实际审阅与验收包](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/curated-60-42320795764.qa.tar.gz)，8,215,048字节，含12张组合画布、6张新增显示页、逐件来源署名和实际验收报告，不含图库原件、目录或SQLite。逐成员及远端摘要核验见[60件交付记录](../data/diagnostics/curated-60-42320795764.json)。
- [新科学元数据版本的逐题相关性证据](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/science-update-42322620502-relevance-evidence-1.tar.gz)，714,448字节、14份JSON；对应[新版本诊断](../data/diagnostics/science-update-42322620502-relevance.json)。

显示检查发现了真实限制：深色剪影在深底对比不足、部分灰区透明透底、白色内填充会遮住下方元素、小尺寸下实验动作和组织细节难以辨认。骨与软骨图上的标签仍可保留入库，但缩略图不能证明标签适合最终成图。逐件观察保存在[48件合并记录](../data/curated-reviews/restored-display-42312769493-merged-48.json)，原文件没有改色、去字或覆盖。

逐题来源、许可、物种适用条件与恢复文件已核对。此前将血管内皮两题列为待定时，额外要求了“已经画成整层”；[原验收规则](ACCEPTANCE.md)明确允许可组合部件。现已纠正为来源确认的单个内皮细胞部件，保留“文件本身不是整层”的限制。当前已恢复目录的正式结果为**100条通过、20条有具体缺口、0条待定**，中英各50/60；原120题、706个结果位置和旧快照均未改写。[纠正记录](../data/relevance-reviews/formal-3.4.3-organs-tissues-organelles-component-rule.json)说明依据。没有物种要求的题明确记为不适用，不新增物种标签。

60件精选的显示和组合审阅完成后，正式验收器又实际运行一次，精选数量与十二类覆盖通过，恢复、六项反例、来源抽检及补充检查继续通过。剩余6条检查均为查询覆盖：总数100/120、中英文各50/60，以及临床流程、实验操作、组学各2/5概念，低于原门槛。报告摘要为 `6e783922756ac898a3ce8d08c5e9e2521b68abe028bd534af65bde600ed5cbd8`。此前[98条确认诊断](../data/diagnostics/formal-query-acceptance-42312769493.json)和[100条待确认阶段记录](../data/diagnostics/current-acceptance-readiness-42312769493.json)继续作为历史证据保存。

组合画布实际检查了背景叠放、对象间遮挡、缩小可读性及画风。结论是60件均可在各自记录的用途和限制内选用；同系列临床场景较协调，混合来源的写实、线描、剪影和立体图不能笼统称为统一画风。深底低对比、原有实色背景和小标签等限制保留，未因此自动排除医学对象，也未去字或修改原件。收录／验收边界及首版基准与新范围的衔接仍待用户选择；当前没有正式网站，也没有自动清理文字的新能力。

上述正式验收只绑定目录 `assembly-42289434868-1`、3.4.3索引及旧候选manifest。后续目录 `assembly-42320673266-1` 已云端装配完成：只有7个文件、5个家族的科学描述与审阅证据改变，收录范围、原件和预览配对均不变，19,473件外观结果全部复用缓存。其[云端120题重放](https://github.com/hujizhou35-cmd/MGA-resource/actions/runs/42322620502)实际确认118题序列不变，溶酶体中英文各新增同一件NIH306 SVG，合计708个位置；要求没有改变。候选非空104/120不能当作相关性分数。新目录的发布清单、补充采用记录和恢复绑定须分别确认，未用旧回执更换哈希冒充新版本通过。

随后逐项关联新序列与已有实际图审：118题的706个位置精确复用，新增两题引用已经实际查看的NIH306 SVG第2格及来源身份，未借用同名PNG。当前新目录的**实际相关性为102/120，中英文各51/60**；共有535个相关、112个不相关和61个待定结果位置。该成绩已记录，原108/120及每语言54/60、每类4/5门槛仍未过；新目录正式发布绑定也仍待完成。记录明确区分候选存在、实际相关、整体发布三种状态。
