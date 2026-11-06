# 两个实验动作缺口的定向核查（2026-11-07）

本轮没有找到可以立即新增入库、同时满足开放再分发和目标动作要求的“小鼠注射”成品组件。离心方向找到了可用的现有操作场景和沉淀管部件，但它们分别表达装样、操作或沉淀状态，不能单独冒充“离心前后分离、上下层含义清楚”的完整命中。没有修改目录、科学字段、检索规则或范围决定，也没有启动新云采集。

逐文件哈希、来源、许可和实际预览依据保存在 [小型核查记录](../data/diagnostics/targeted-experimental-gaps-20261107.json)。这份记录是调查结果，不是新素材导入清单。

## 小鼠注射：仍保留真实缺口

复用了此前 [7 件精确预览核查](../data/diagnostics/lysosome-mouse-injection-gap-20261107.json)：公共和内部元数据共 11,884 个来源上下文中，常见注射路线提名没有找到同一来源上下文同时明确“鼠”和“注射”的作品；已实际看过的悬尾、保定筒不含注射接触动作。本轮不重复下载它们。这个结果不能证明所有未审图片绝无注射场景。

本轮优先读官方介绍和记录，聚焦 SciDraw/Bioicons、Servier、Science Figures、Commons，以及 Addgene 的明确图像来源说明：

| 线索 | 实际查到的证据 | 本轮处理 |
|---|---|---|
| [Science Figures 8583](https://sciencefigures.org/figure/8583/rat_restrainer_intravenous_tail_injection/) | 官方作品标题为 Mouse Restrainer Intravenous Tail Injection；页面提供 SVG。链接到的 [Open Design 1.1 §2.1.2](https://opendesign.fyi/license/) 带有 “not redistributing in same or similar way” 条件。页面标题写 mouse、路径写 rat，身份也尚未独立核实。 | **不收进下载库**。我们的同类素材再分发不能只凭“open”宣传词认定获准；没有下载原件或把它标成合格候选。 |
| [Addgene 鼠注射路线](https://blog.addgene.org/common-injection-routes-in-mice) | 官方 Figure 1 注明由 BioRender 创建。 | 不把机构文章的公开可阅读性当作组件开放再分发授权。没有取图或拆图。 |
| [Servier Rodents](https://smart.servier.com/category/general-items/animals/rodents/) | 官方分类共 21 条，介绍及标题以鼠、兔、豚鼠等动物对象为主，当前官网声明 CC BY 4.0。 | 本轮未定位到独立鼠注射成品；没有把独立鼠图和注射器标题的共现当作动作。未逐图重审全部 21 条，也未把官网新版许可覆盖到现有历史文件。 |
| [Commons Tail vein injection 01](https://commons.wikimedia.org/wiki/File:Tail_vein_injection_01.jpg) | 文件页明确是大鼠尾静脉注射摄影，CC BY-SA 3.0，并有相机信息。 | 照片且对象为 rat，不符合本轮 mouse 插画目标；未下载。 |
| [Commons 腹腔注射分类](https://commons.wikimedia.org/wiki/Category:Intraperitoneal_injections) | 这页列出 11 个条目，主要为人类腹膜示意与实验视频。 | 未找到可直接收录的小鼠注射独立插画。 |

GitHub 和网页定向搜索也使用了 mouse injection、intraperitoneal SVG、tail vein injection Creative Commons、centrifugation SVG GitHub 等组合；很多返回电脑鼠标输入注入、付费图库或论文整图。它们只是检索线索，没有被当成适用医学素材。本轮没有扩展为全网穷尽搜索，也没有从论文结果图或研究设计图拆出组件。

## 离心：已有可复用部件，仍缺一张完整动作匹配

在冻结目录 `assembly-42289434868-1` 中重新提名 centrifugation、pellet、supernatant、density gradient 等相关来源上下文；同时复用已经下载的云端小联系表，实际显示并查看 `lab_equipment-centrifuge.png`。以下三件都已在现有公开候选清单中，不是新增素材。

| 实际文件 | 观察到的内容 | 能支持／不能支持 |
|---|---|---|
| [Togo 1571](https://doi.org/10.7875/togopic.2023.007)，PNG，原件 SHA 开头 `3c5257f8cbf79` | 人站在打开的大型离心机旁，手位于转子和样本区域；是绘制的设备操作场景。 | 可支持装样或设备操作场景。看不到旋转过程、分离后的层次或前后比较。 |
| [Togo 2012](https://doi.org/10.7875/togopic.2024.171)，SVG，原件 SHA 开头 `afdbbdabac15` | 戴手套的人在打开的台式离心机旁，一手举试管，另一手接近机器。 | 可支持拿取样本／操作场景。图中没有可辨的分层液体，不能证明分离完成。 |
| [Togo 727](https://doi.org/10.7875/togopic.2020.140)，PNG，原件 SHA 开头 `caec5941b3af` | 倾斜离心管底端有浅色沉淀块。 | 可作为 pellet 状态部件。没有清楚画出的上清液界面、前态或离心动作。 |

三件的来源记录均为 CC BY 4.0；1571、2012 署名 erico，727 署名 @arabitom，并保留 DBCLS TogoTV。1571、727 的官方文件页 HTML 也实际读取到作者、题名和 CC BY 4.0 结构化声明。观察只绑定表中具体文件；没有跨到同作品其他格式。

此前已精确视审的 [Bioicons/DBCLS centrifuge user](../data/visual-reviews/gap-candidates-42280422940-scientific.json) 同样只能支持装样／设备操作，不能证明分离层次。本轮保留它已有的判断，不重复云审核。

这些素材可以供后续绘图时有意识地组合；如果需要一张离心分离图，仍须针对具体任务补上正确的样本前态、产物含义和连接关系，并另外验收。不能把“两个素材同时存在”记成已经具有完整工作流，也不能自行添加转速或时长。

## 新线索 XBio：内容相关，但不直接入库

[Explore Biology 的原始 Flickr 作品](https://www.flickr.com/photos/204521145@N06/55359353959/) 署名 Gb Kim，解释密度梯度分离；[Commons 文件页](https://commons.wikimedia.org/wiki/File:XBio_illustration_%E2%80%93_Equilibrium_Density_Gradient_Centrifugation.png) 标明 CC BY 4.0，并记录了 Flickr 许可核查。仅取了一张 960×640 的小预览（186,746 B），没有下载原件或整库。

实际预览是带大标题、文字标注和箭头的四阶段教学成图：悬液、带配平管的转子、旋转后的姿态和轻重颗粒分离。它确实比静态离心机更接近动作目标，但属于完整教学图；本轮把它记为 **范围待定、未导入**，没有借补缺口放宽范围。此处也没有独立认证其“等密度梯度”全部细节。若将来只采用其中某个部件，需要单独明确裁取范围、保留署名并检查科学含义，不能沿用本次整图观察自动盖章。

## 本轮验收边界

- 已实际看：已有云包里的 3 件 Togo 精确文件，以及 1 张新增 XBio 小预览；另复用已有 centrifuge user 和鼠动作核查记录。
- 已核对：Togo 3 件的原件／预览 SHA、双归档成员与冻结候选清单一致；云包实际 sheet 与 mapping 的 SHA 绑定；XBio 下载预览的字节哈希与尺寸。
- 没有做：原生 Illustrator 编辑测试、新目录导入、检索改写、整库下载、论文抠图或新的云任务。
- 结论：**鼠注射仍缺；离心已有动作部件与沉淀部件，但完整分离语义仍未通过。新增合格导入项为 0。**
