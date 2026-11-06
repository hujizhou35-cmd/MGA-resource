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
