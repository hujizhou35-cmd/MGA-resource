# 统一检索核心（数据阶段）

`mga_resources/search-core.mjs` 是新查询解析、硬条件、变体选择、家族归并、排序和分页的唯一实现。它不依赖 Node 或 Cloudflare API。`search-cli.mjs` 只处理 JSON 输入输出；Python 只调用核心、执行核心生成的 SQLite 条件并读回选中文件的完整元数据。未来 Worker、网页 API、CLI 和可选 MCP 应复用此模块，不重写排名。

这一步没有搭建网站。输出是待审阅候选，不代表医学相关性、画风或 Illustrator 验收已经通过。

## 输入与返回

旧调用 `search(db, query, limit=10, filters=None)` 保留，返回列表。完整调用：

```python
from mga_resources.catalog import search_page

page = search_page(
    "catalog.sqlite", "小鼠", limit=10,
    filters={"color_mode": "color", "view": "lateral"},
    mode="design", seed="study-a",
    project={
        "style": {"detail": "minimal", "series": "official-series-id"},
        "selected": [{"asset_id": "...", "family_id": "...", "file_sha256": "..."}],
    },
    history={"snapshot_id": "local-project-history-v1", "adopted": [
        {"family_id": "...", "file_sha256": "...", "status": "adopted"}
    ]},
)
```

`project.selected` 是同项目实际选用的文件。仍满足此次硬条件的选中项优先保留；不再满足条件时单列原因，不强行放入结果。`history.adopted` 只有明确标记 `status: adopted` 的最终采用记录参与适度降权；浏览与试下载不计入。无需论文正文、账号或云端用户资料。

硬筛选支持 `source/license/distribution_status/category/format/species/state/view/role/object/depiction_kind/color_mode/palette_traits/representation/detail/realism/series/asset_id/family_id/file_sha256/transparent`。结构化字段的列表表示允许其中任一种；不同字段必须同时满足。自然语言明确要求“正面与背面”时，两种视角必须同时有证据。未知值不满足硬条件。`filters.preferences` 或 `preferences` 参数中的字段只用于软排序。

颜色对应 `file.appearance.color.mode`；表现方式、细节、写实程度和系列分别对应独立外观轴。外观的原件及预览哈希必须与选中文件一致，画风须有文件级证据引用。不能从 SVG/PNG、来源名称或路径数量推断画风。

颜色模式未知、未测量或明确需要复核时，暂定调色标签不能满足硬筛选。已知模式且置信等级为 medium 的真实机器测量仍可检索，并保留其不确定性说明；这不是视觉验收通过。

自然语言中明确的颜色、侧视/截面、正常/病理和完整范围被解析为硬条件；“偏好/优先”可将颜色、画风和视角变为软偏好。较复杂或有歧义的句子应使用结构化参数，并查看返回 `query_plan`。显式科学信息取自具体文件/原始来源变体，或直接来源标签；标签命中仍需独立医学审阅。未知科学状态不会由家族其他成员补齐。

每个结果包含：

- 原有家族字段，兼容现有汇总与审阅工具。
- `selected_file`：本次实际符合条件的文件，包含原件哈希、预览、归档和外观字段。
- `preview` 与 `download`：与 `selected_file` 一致，不能另取 `files[0]`。
- `selected_source` 与 `selected_science`：这次变体的来源和科学条件依据。
- 真实验证状态、是否为同项目固定选择、排序分数；没有“验证过所以永久优先”的加分。

`next_cursor` 绑定目录哈希、排名版本、查询计划、模式、种子、完整历史快照、项目记录及候选窗口。相同输入产生相同排序；变更任何绑定后使用旧 cursor 会明确报错。页大小可调整，已返回的家族不重复。

## 排名与范围

顺序是：文本对象候选 → 文件可用性与许可 → 科学和明确外观条件 → 家族内选择合适变体 → 家族归并 → 轻量重排 → 分页。

SQLite v5 / ranking `shared-search-3.2.0` 的 `file_variants` 保存逐文件、逐原始科学上下文的记录，并预计算精确词项集合、相似度特征和家族种子键；`file_variants_fts` 仅作中英文粗召回。核心生成 SQL 条件，先对全部候选完成精确对象词项及硬条件过滤，再用窗口函数选出每个家族的具体文件，最后截取候选窗口。SQL 向排名核心仅返回小型引用/分数/特征，不传整个家族或全部命中文件。选出最终 10 个候选后才读取详情。未知记录的数量及小型样本单列在诊断中。

v3.2 区分物种与主要对象：`Mouse Brain` 的物种可以是 mouse，但不能作为完整动物查询的合格对象。`taking blood sample` 是采血动作，`blood collection tube` 是设备；电化学 cell 也不作为生物细胞。具体器官也不能由宽泛标签独立证明；明确的来源对象字段或描述中的直接绘制说明可以补充通用标题。`heart cell` 的主要对象是细胞；并列要求 heart 和 liver 时，两组对象须同时成立。不能确定主要对象或视角的文件保留在诊断中。人工科学审阅须绑定原件哈希、预览哈希及两个归档位置，才能补充对应文件；冲突字段保持未知。返回依据区分来源字段、直接来源标签和人工审阅，完整动物已经目视确认只由有效人工证据标记。

默认窗口为 75 个**合格家族**，可在 50–100 内调整。更多家族保留到后续窗口和分页，不被静默丢弃。MMR 使用标题、科学属性和画风系列的轻量相似度，只减少同一次展示的重复，不根据相似度删除或合并作品。没有来源硬配额。每个固定窗口独立重排：跨页可重算同一个窗口并从已绑定位置继续，不丢失尚未展示的家族；跨窗口不延续相似度惩罚。相似度缓存和只计算所需排序前缀减少 CPU 工作，不改变同一窗口结果。

- `explore`：在相关性接近的合格候选中，结合种子、采用历史和轻量 MMR 提供替代候选。
- `design`：额外应用项目明确的风格方案，保留已选合格文件；明确参数与项目方案冲突会报错。
- `exact`：不进行种子轮换或采用历史降权，仍执行科学/文件条件及家族去重。

默认种子固定，便于复现。跨项目轮换需要调用方传入不同项目种子；本模块不会在刷新时偷偷生成随机种子。稀缺场景只有一个合格家族时仍返回该家族并报告稀缺，不混入错误物种或状态补足数量。

## 对照、成本与验收

旧规则保存在 `search_legacy()`，使用原有家族级 SQLite BM25 和宽松视角偏好，仅用于新旧对照。正确性检查点 `ae42b8c` 还保留了完整候选版本 v2 的真实诊断。旧索引不会自动充当新索引；已有冻结 JSONL 可用 `rebuild_search_index()` 重建 v5，保留输入目录原始字节和哈希，原件和预览均可复用。Node.js 22+ 是构建和本地查询依赖，核心模块本身可由 Worker 导入。

v3 的 `prepareWindowSearch()` 生成 SQL 计划和版本绑定；`finishWindowSearch()` 执行纯排名小核心；`materializeResult()` 将选中引用与详情组合。直接内存检索与 SQLite 窗口路径使用同一套排序小核心，并用跨窗口、采用历史和项目固定选择的用例验证顺序一致。v3 精确词项还消除了一处旧别名问题：“离心”不能因为是“离心机”的子串，就把仪器标签当作离心操作证据。

`scripts/diagnose_search.py` 使用真实目录元数据，完整运行既有 120 条基准、宽查询及多种子候选对照，记录实际返回文件、未知条件、载入 payload 大小、耗时和候选重叠。报告不把非空结果、标签命中、Top 10 重叠或多种子覆盖当作医学相关性通过，也不把“第一候选不同”当作实际最终采用已经不同。现有 108/120、每语言 54/60、分类和视觉验收门槛保持不变。

Node 本机耗时不等于 Cloudflare CPU 用量。宽泛查询可能仍有大量合格文件，需要在部署前测量 D1/Worker 的实际成本；如不满足免费边界，应继续使用同一核心生成的过滤计划、预计算或分批候选策略。不能通过在硬条件前截断来掩盖性能问题。

本机报告分别记录纯重排核心、SQLite 查询、Node 进程往返、完整 `search_page()` 和实际返回字节数。快速重排不表示完整检索已经达到 Worker Free 限额。`scripts/validate_search_behavior.py` 生成真实目录的功能观察和空白采用记录；最终采用必须由实际审阅决定填写，不能自动把第一名或试下载当作采用。
