# MGA-resource · mainly-for-agent

[English](../README.md) · **简体中文**

面向绘图 Agent 和研究者的免费医学素材库。查找器官、细胞、动物、临床场景和实验器材等可复用组件，直接获取预览、命中的原件与署名。

**[打开素材工作台](https://mga-resource-mainly-for-agent.pages.dev/?lang=zh) · [Agent 接口](https://mga-resource-mainly-for-agent.pages.dev/agent?lang=zh) · [项目介绍](https://mga-resource-mainly-for-agent.pages.dev/about?lang=zh) · [素材来源](https://mga-resource-mainly-for-agent.pages.dev/sources?lang=zh)**

## 为什么做这个项目

MGA-Designer 在构建图像摘要时，需要可重新组合的图，而不是整页既有研究设计或结果。逐张从论文中提取可用部分很慢；在许多图库间跳转，也使 Agent 难以比较画风、获得准确文件并保留许可。MGA-resource 把这些步骤接成一条可复现的路径。

## 开始使用

在工作台点类别或输入对象名称。用颜色、画风、格式、来源等条件缩小范围，查看真实预览，再下载或加入项目。一个项目可以固定画风和已选的具体文件；换一个项目或探索轮次，可让其他合格作品获得展示机会。本机记录可导出给 Agent。

```sh
curl -sS 'https://mga-resource-mainly-for-agent.pages.dev/v1/search?q=mouse&limit=5&response_profile=compact'
```

Agent 查看 `preview.url`，下载选中结果的 `download.url`，核对 `download.sha256`，保留 `selected_source.provenance`。不用访问原图库或解压整包。[可运行示例](../site/public/agent-example.mjs) · [OpenAPI](https://mga-resource-mainly-for-agent.pages.dev/openapi.json) · [目录与筛选取值](https://mga-resource-mainly-for-agent.pages.dev/catalog-info.json)

## 怎么构建

![调查来源、云端采集、固定原件、建立索引、审查验收、免费发布](../site/public/workflow-zh.svg)

2026-11-06 开始固定来源版本，在云端分批采集，逐文件校验哈希、识别结构、生成预览并归并家族。11-07 根据图像内容明确边界，保留可用的带标注对象，排除以文字框为主的整张流程图、研究设计教学图和结果图。11-14 完成组件检索和网站交付验收后上线。[建设与验收记录](CLOUD-QA-RECOVERY-20261107.md)

## 素材来自哪里

当前提供 **19,473 个可下载原件，7,838 个素材家族**。例如 NIH BioArt 的精细插画、SciDraw 的实验及动物素材、Health Icons 的临床图标、Reactome 的结构组件，以及 Bioicons 和 DBCLS Togo 的生命科学插画。Bioicons 中固定历史版本的 Servier 作品保留逐件署名与许可说明。

[全部实际发布来源、规模与用途](SOURCES.md) · [在线来源表](https://mga-resource-mainly-for-agent.pages.dev/sources?lang=zh)。同一原件可以有多个来源记录，来源小计不可直接相加。

## 验收与边界

| 记录 | 结果 |
| --- | --- |
| [组件检索正式验收](../data/acceptance-reports/component-43481252923.json) | 120/120（指定任务和语言） |
| [原始历史检索基准](CLOUD-QA-RECOVERY-20261107.md) | 102/120（另一套任务） |
| [首次网站交付](../data/acceptance-reports/website-20261114.json) | 142 请求一致，38,748 原件及预览对象核对字节与哈希 |

候选需按实际绘图任务核对，SVG 不保证为纯矢量；带标签素材如需清理，修改工作副本并保留科学含义。代码为 MIT（上游声明除外），图片维持逐件许可。免费搜索受 Cloudflare 日额度影响，暂时不可用与无匹配结果分别返回，已知静态链接可继续使用。

维护者从 [技术与版本记录](WEBSITE-ARCHITECTURE.md) 查阅流程。批量素材不进入 Git，不在本机整库下载。
