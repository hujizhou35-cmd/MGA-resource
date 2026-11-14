# 2026-11-06 素材来源调查（历史快照）

此页保留调查时的状态；[当前正式来源见这里](SOURCES.md)。

核对日期：2026-11-06。机器可读配置在 [sources.json](../data/sources.json)。本文件记录来源的性质和收录决策，不把“登记了来源”当成“已取得素材”。

## 收录对象

第一版收科研插画、图标、示意图和二维三维渲染图，接受矢量与位图。照片、显微图、论文实际数据图和原生三维模型不在首版范围。先读网站介绍、看代表作品，判断对象、画风和用途，再选择下载格式。透明背景是便利属性，不是判定医学质量的依据。

## 首批可配置来源

| 来源 | 内容与用途 | 格式与取件方式 | 许可和注意事项 |
| --- | --- | --- | --- |
| [Bioicons](https://github.com/duerrsimon/bioicons) | 生命科学插画、图标和实验部件，作者与风格多样 | 固定 Git commit 的单件 SVG；保留作者目录与元数据 | 按图许可，不能套用仓库代码许可；Servier 作者条目暂时单列 |
| [Health Icons](https://github.com/resolvetosavelives/healthicons) | 临床、公卫、器材和人物图标 | 官方 SVG 目录与 meta-data.json，包含线稿/填色和尺寸变体 | 图标 CC0；多个样式不算多个独立概念 |
| [Arcadia](https://zenodo.org/records/18395998) | 模式生物的剪影、三色描边与透明位图 | 官方 v1.2 ZIP，SVG/PNG；仅在云端下载解包 | 官方声明 CC0；保留 Arcadia 与已提供的 PhyloPic 来源关系 |
| [SciSVG](https://github.com/SaveenaSolanki/SciSVG) | 小型细胞、分子、实验和生信组件库 | assets/ 与正式逐图 metadata | 素材 CC BY 4.0；代码 MIT；逐图例外优先 |
| [KYU49 Lab Equipment](https://github.com/KYU49/SvgCollectionOfLabEquipment) | 生化/化学实验器材及其部件、开合/装液状态 | svg/ 目录；排除整张 Summary 图 | CC BY 4.0，保留作者；部件与状态需作为家族关联 |
| [Open Pain Illustration](https://github.com/DigitalHealthZurich/open-pain-illustration) | 人体正背面及区域着色，用于症状位置示意 | illustration/ 的 SVG | CC BY 4.0，附论文引用；不导入 SNOMED 映射包 |

“configured”只表示已提供采集配置；实际发现、下载、成功、失败、重复和验收数量必须由云端任务报告给出。这六个来源是首批通路验证，**不是完整库的收尾范围**。

## 继续整合的主力来源

- [NIH BioArt](https://bioart.niaid.nih.gov/faqs)：专业医学插画师与学科专家合作的器官、动物、细胞和过程插画。重点保留精细主视觉，逐条记录 Public Domain 或具体许可。
- [Reactome](https://reactome.org/icon-lib)：细胞、分子、受体、细胞器和组织组件。图像 CC BY 4.0；记录设计者。研究期间官方整包链接返回 404，必须以实跑结果说明可用取件路线。
- [SciDraw](https://scidraw.io/)：动物、器官、细胞和实验场景，贡献者画风多样，逐图记录作者、DOI 与许可。
- [SwissBioPics](https://www.swissbiopics.org/help)：带细胞器位置和生物分类信息的细胞图。官方提供 API，图片 CC BY 4.0。
- [DBCLS Togo](https://dbarchive.biosciencedbc.jp/data/togo-pic/20180629/README_e.html)：官方档案提供 CSV、PNG、AI、SVG；明确允许按 CC BY 4.0 取得、再分发及建立派生数据库。与 Bioicons 的 DBCLS 图去重。
- [BioCartoon](https://github.com/josephrich98/biocartoon)：优先复用其多来源目录设计、署名处理和发布方法。已知 catalog URL 出现 HTTP 403，保持 blocked_access，不重复或绕道请求该目录。已知独立原始来源仍可按其各自公开接口建设适配器，但不拿它们恢复被拒目录。

## 特殊来源及定向补缺

- [Servier](https://smart.servier.com/using-smart-images-what-you-can-do/)：医学插画价值高；与本项目相关的自动获取及独立图片库说明尚需明确，暂不纳入公开镜像。
- IAN/UMCES 用于环境健康与暴露；PhyloPic 用于物种剪影；OpenMoji 用于人物、饮食及生活方式图标；Wikimedia Commons 用于具体解剖或操作缺口。它们按缺口选取，不把全部非医学内容灌入库。
- [SciGlyph](https://github.com/ZengZichao/SciGlyph) 的双语字段、画风和形态分类可借鉴。素材声明 AI 辅助生成，仍需科学与视觉检查；其 MCP 的样例搜索不能作为已具备全库检索的证据。

## 采集记录

每个来源报告至少列出：固定版本、发现条目、计划取件、实际取得、失败、许可待核、实际媒体类型、完全重复文件、逻辑素材家族、预览可用数量、人工或独立 Agent 复核数量。未知作者、物种或科学状态保持未知。

图片不进入普通 Git 历史。本机只处理代码、少量元数据与定量受限的审阅样例；整库下载、预览生成及素材包发布在 GitHub Actions 云端执行。
