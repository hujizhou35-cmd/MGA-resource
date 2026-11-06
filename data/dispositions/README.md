# 来源缺口处置

`scripts/prepare_source_dispositions.py` 只处理元数据，不下载图库或原件包。
它把处置绑定到明确的汇总、完整发现清单、失败证据和原始包哈希，生成
`decisions.json`、逐项 `selections/`、候选发布清单及待处理报告。
这些文件不是验收通过证明。最终来源及 Togo 续采进入新汇总后必须重新生成。

```powershell
.venv\Scripts\python.exe scripts/prepare_source_dispositions.py `
  --catalog build/assembly-third/assembly-42254549321-1.catalog.jsonl `
  --assembly-report build/assembly-third/assembly-42254549321-1.report.json `
  --assets build/release-metadata/collection-20261106.assets.pages.json `
  --metadata-dir build `
  --output build/dispositions-third `
  --file-review data/visual-reviews/togo-42249870031-sample.json
```

缺少已固定的报告、失败清单或发现清单时，可以加 `--fetch-metadata`。
只允许这些 JSON/JSONL，须匹配 GitHub Release 的完整 SHA-256 和文件大小。
相同内容哈希的已有元数据可复用。访问拒绝不自动重试。
可用重复的 `--source` 限定本轮分析来源；不写则分析已有的五个主力来源。
新汇总已经分离公开/内部目录时，还必须传入 `--internal-catalog`
指向同一 assembly 的 `assembly-<run>.internal-catalog.jsonl`。
它须匹配汇总和 GitHub 的哈希、大小及数量，用于辨别“已取得但不公开”和“尚未取得”；
缺失变体只能引用公开目录里仍可用的格式，不能用已排除的名牌或照片充当公开 counterpart。

处置规则：

- 用完整 discovery 的 `(URL, upstream archive member, format)` 减去实际已保存文件。
  ZIP 共用 URL 时必须区分成员；旧失败记录没有成员且无法唯一对应时保持待处理。
- 某件缺一个格式时，仅记录 `unavailable_variants`，引用保留格式的真实 SHA。
  不为没取到的文件编造 SHA，也不删掉同件已经成功的格式。
- 只有整件所有未取得描述符都有明确失败证据，才生成整件隔离。
  临时网络错误、未知范围、无法唯一匹配的失败均保持待处理。
- 仅固定 Git commit、路径和原 Git blob 完全一致时，可跨 metadata discovery 哈希复用旧失败证据。
  NIH、Togo 等可变 URL 不适用。后续成功但尚未进入当前汇总的项目要求重新汇总。
- 已保存但明确预览失败的原件按文件 SHA 隔离；原包保留，不要求整件重新下载。
- `--file-review` 支持实际审阅产生的精确文件决定。输入每项须有 source_id、原件 SHA、
  disposition、reason、reviewed_by、reviewed_at。仅 `scope_pending` 观察不自动排除或通过；
  如审阅决定首版暂不发布，明确写 `exclude_from_v1`，同时保留 `scope_status=scope_pending`。
  不按 PNG 扩展名批量判断照片。
- BioCartoon 只引用已有 registry 的拒绝记录及其哈希，标记 `historical_recorded_denial`，
  不重新访问已被拒绝的目录，不声称保存了当时未留存的原始 HTTP 日志。

报告中的 `pending_count` 是待处理记录条数，可能包含同件多个格式，不是独立素材数量。
演练摘要保留输入和结果哈希及无文件的精确条目，完整结果在上述 build 输出目录；
复现必须使用相同输入字节。验收工具仍要求原来的 120 条检索、视觉审阅、恢复证据等条件。

## 公开范围的一致过滤

`mga_resources/scope_policy.py` 直接消费相同 `file-review` 证据格式，
默认读取 `data/visual-reviews/` 和 registry 的 `public_scope_review_paths`，
不另建一套批准记录。实际审阅的 `exclude_from_v1`、`quarantine` 及明确待复核决定
按原件 SHA 生效；预览完整性问题另绑定预览 SHA。文字属性未知、文件标题命中某个词
或是 PNG/SVG 格式本身，都不是排除依据。

纯文字分子名牌的公共范围类别为 `content_scope_reason=text_only_molecular_nameplate`。
只有实际审阅、绑定原件/预览 SHA 的 `exclude_from_v1` 才会生效；
含分子标签的结构受体、细胞或机制部件仍可收录，未审文件不因标题或来源自动排除。
此类别沿用同一个文件级决定，不改变许可、不重写原件，也不把已收录结构部件改成纯文字。

采集器保留完整 discovery、内部 records 和 receipts；只有固定上游内容哈希、URL、
格式及大小都吻合的既有范围排除，才可以在再次取件前跳过。
汇总先保存 `internal-catalog.jsonl`，再物理过滤公开 `catalog.jsonl`、SQLite 及检索候选。
`scope-report.json` 保留范围理由、审阅文件哈希、原件 SHA 与原始归档位置。
内部诊断记录带 `catalog_visibility=internal_diagnostic`，不能直接用作公开索引。
来源采集/机器状态仍按内部完整 inventory 计算，不因公开数量减少而伪装采集失败。

验收和 clean publish pack 都消费同一范围模块，阻止已排除或明确待复核文件重新进入
公开下载清单和恢复包。晚于旧疑点的实际媒体类型审阅可以解决疑点，但不能覆盖独立的
“完整信息成品”排除或预览完整性问题。源码原件和旧审阅历史仍留在云端。

Commons 6 件实际云元数据的针对性检查见
`commons-component-scope-check-20261107.json`：01–04 保留，05–06 排除；
公开索引和下载规划均只有 4 个原件。该检查不是全库验收或云端重打包/恢复完成声明。
