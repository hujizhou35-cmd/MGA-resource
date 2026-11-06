# 精选对象审阅记录

`selected-object-components-42289434868.json` 是对冻结目录与候选下载清单的 **48 件、48 个不同家族** 的实际对象审阅。它不改变目录、固定 120 条检索或用户尚未决定的范围边界。

| 类别 | 不同家族数 |
| --- | ---: |
| 动物、人物、器官、组织与血管、细胞、分子、微生物、实验设备 | 各 5 |
| 细胞器 | 4 |
| 实验操作 | 4 |
| 临床流程、组学与数据 | 各 0 |

本次独立 Agent 实际显示并查看了 9 张已有云端联系表，复核了各表 SHA-256。每条精选记录同时保留原件和预览的 SHA-256、两者的 archive 成员、精确来源记录、原视觉审阅文件哈希与 JSON pointer，以及本次根据图面手工写出的用途和限制。检索排名及目录分类不作为其适用性证据。`mapping_candidates` 使用最终目录自身的 `family:file-index`，可合并到检索验收 mapping 的候选字典，但不能改变查询排名。

图面可辨认、来源名称支持及当前可复用范围已审阅。细字原分辨率可读性、深色背景叠放和实际组合画布协调性仍需在具体选用场景检查；本次没有执行新原生编辑验证或去字操作。科研精细结构与疾病状态未被普遍验证。Commons 01–04 的带标签对象正常入选；05、06 不在本清单。静态共培养没有冒充培养操作；同一溶酶体的 PNG/SVG 也没有重复计算家族。

当前还差 12 个符合对应类别的不同家族，且质量检查不齐，**不满足“60 件、12 类各 5 件”的完整门槛**。临床流程与组学图没有为了补数而绕过待定边界。

执行绑定核验：

```powershell
.venv\Scripts\python.exe -X utf8 scripts/audit_curated_review.py --review data/curated-reviews/selected-object-components-42289434868.json --manifest build/dispositions-final-scope-42289434868/candidate_publish_manifest.json --output data/curated-reviews/selected-object-components-42289434868.audit.json
```

核验报告 `binding_passed` 只说明记录、真实文件及证据字节一致；不会把程序输出当成看图，更不等于整体发布验收通过。
