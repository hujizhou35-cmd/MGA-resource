# 当前候选包的云端恢复验收

当前候选包已在云端完成真实构建、独立下载恢复和范围核验。它是QA预发布，整体素材验收与网站发布仍未通过。

| 实际结果 | 数量／状态 |
|---|---|
| 选中原件 | 19,473 |
| 去重后的原件与预览对象 | 38,748 |
| 干净分包 | 13个，合计3,342,746,415字节 |
| 已排除／待定内容混入 | 0 |
| Commons对照 | 01–04保留，05–06排除 |
| 范围验收失败／待提供证据 | 0／0 |
| 实际显示审阅 | 48件、24张检查页、336个显示条件 |
| 恢复所用代码的CI | 526项通过（提交 `4ca726b`） |

[构建任务](https://github.com/hujizhou35-cmd/MGA-resource/actions/runs/42309798276)和[独立恢复任务](https://github.com/hujizhou35-cmd/MGA-resource/actions/runs/42312769493)是两次不同的运行。恢复任务确实重新读取远端索引、manifest和13个分包，核对完整压缩流、精确成员、大小及SHA-256；没有拿构建自检代替。原件只在云端处理，本机接收元数据和有界小预览。

可核对的资料：

- [QA候选归档](https://github.com/hujizhou35-cmd/MGA-resource/releases/tag/publish-qa-20261107-42289434868-3.4.3)，明确标为非正式发布。
- [独立恢复回执](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/qa-20261107-42289434868-3.4.3-42312769493-1.restore_evidence.json)。
- [48件精选预览与检查页](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/restored-curated-42312769493-1.tar.gz)，约8.08 MB，不含图库原件。
- [范围审计与逐件显示审阅元数据](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/qa-acceptance-42312769493.metadata.tar.gz)，约0.64 MB，不含图像。
- [正式验收输入与未通过报告](https://github.com/hujizhou35-cmd/MGA-resource/releases/download/acceptance-20261107/acceptance-evidence-42312769493.metadata.tar.gz)，419,796字节、7份元数据文件；远端大小和SHA-256均已核对，摘要为 `ef000ba001925cf5124b72cf90a45f281559673501f37d16ff358890263ad3e7`。
- [全部版本与哈希记录](../data/diagnostics/cloud-qa-recovery-42312769493.json)。

显示检查发现了真实限制：深色剪影在深底对比不足、部分灰区透明透底、白色内填充会遮住下方元素、小尺寸下实验动作和组织细节难以辨认。骨与软骨图上的标签仍可保留入库，但缩略图不能证明标签适合最终成图。逐件观察保存在[48件合并记录](../data/curated-reviews/restored-display-42312769493-merged-48.json)，原文件没有改色、去字或覆盖。

现有看图判断、逐文件映射和本次恢复证据已关联，并实际运行正式验收器。恢复、六项反例及补充检查均通过；100条有视觉适用候选的查询仍保留正式判断待确认，20条已记录具体缺口。文件成功恢复不能代替逐题来源、许可、物种及所选素材确认，也没有把旧空值直接改成通过。对应记录见[当前验收证据关联](../data/diagnostics/current-acceptance-readiness-42312769493.json)。前面的云端恢复诊断保留证据关联尚未完成时的快照。

整体门槛仍未通过：原120条查询的视觉相关性是100/120，未达到108/120；精选只有48个家族，尚未满足60件及十二类各5件；技术邻接板也不等于真实科研构图的遮挡和画风验收。四项收录／验收边界仍待用户选择。当前没有正式网站，也没有自动清理文字的新能力。

本次结论只绑定目录 `assembly-42289434868-1`、3.4.3检索索引及当前候选manifest。后续改变收录范围或文件配对时，必须重新核对对应版本，不能把这一候选包的结果挪作新版本证明。
