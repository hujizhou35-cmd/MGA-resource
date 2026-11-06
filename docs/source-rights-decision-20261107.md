# Servier 历史素材收录决定（2026-11-07）

用户已明确选择：将 Bioicons 固定历史版本中的 1,375 件 Servier 素材按 **CC BY 3.0** 纳入 MGA-resource，保留完整署名、固定来源和当前 FAQ 的实际冲突说明。此决定不需要再次确认。

范围为 Bioicons 提交 `d29e766ea7580b8063c4f47b29e872db40a4d979`：1,374 件位于 `Servier` 作者目录，1 件位于 `ModifiedFrom_Servier`。使用已有公开 GitHub 副本，不抓取 Servier 官网图库，不将历史文件改标为 CC BY 4.0。原件字节保持不变；改作保留上游的改作标记，不猜测未注明的改作者。

依据是固定版本的 [README](https://github.com/duerrsimon/bioicons/blob/d29e766ea7580b8063c4f47b29e872db40a4d979/README.md)、[CC BY 3.0 目录许可](https://github.com/duerrsimon/bioicons/blob/d29e766ea7580b8063c4f47b29e872db40a4d979/static/icons/cc-by-3.0/LICENSE) 和 [作者表](https://github.com/duerrsimon/bioicons/blob/d29e766ea7580b8063c4f47b29e872db40a4d979/static/icons/authors.json)。[CC BY 3.0 法律文本](https://creativecommons.org/licenses/by/3.0/legalcode.en)第 3 节允许合集和分发，第 7(b) 节说明后续许可安排不撤回已授许可。

同时保留真实冲突：[当前 Servier FAQ](https://smart.servier.com/using-smart-images-what-you-can-do/)在教育游戏和应用问答中写有“不作为独立图库或合集销售”的限制。不能删除这条说明，也不把它改写成“许可证未知”或“只限医护人员”。本次用户选择按既有 CC BY 3.0 分发链收录；当前 FAQ 和标准 CC 授权之间的差异仍随每项元数据保留。完整历史核查保存在 `data/diagnostics/servier-rights-assessment.json`。

每项记录含原作者、CC BY 3.0 链接、固定文件来源、来源提交、许可依据、决定日期及 FAQ 冲突说明。Servier 仍是主力来源，在注册表中标为 `integrated_via_pinned_bioicons`；目录引用 Bioicons 下的同一批记录，不重复采集或重复计数。只有完整的固定 ID 集合与云端原件证据匹配后，才会标记该来源采集完成。

许可已确认不等于文件已下载。决定时旧云端记录中有 1,254 件成功素材可安全复用；这 1,375 件 Servier 仍待云端采集。另外 3 个 BSD 图标及 1 件论文图已分别补全许可版本和作者，仍按实际收据计获取状态。

零重复下载的增量范围保存在 `data/collection-plans/bioicons-incremental-20261107.json`。Servier 两段为 `offset=491, limit=1103` 和 `offset=1595, limit=272`。全体当前建议采集为 1,538 件（Servier 1,375、原生 SVG 修复候选 159、其他许可修复 4），与旧成功 1,254 件的索引没有重叠；另 38 件已知问题保留隔离说明。
