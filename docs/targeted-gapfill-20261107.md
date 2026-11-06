# 定向补缺：7 件独立来源素材

本次只补充医学绘图所缺的组织示意和临床研究流程部件，不更改冻结的 benchmark，也不通过期望 ID 绕过搜索。素材来源清单为 `data/curated/gapfill-20261107.json`；逐项官方元数据快照及未选原因为同目录的 `.evidence.json`。

| 来源与作者 | 素材和使用范围 | 原许可 |
| --- | --- | --- |
| [Commons / Pbroks13](https://commons.wikimedia.org/wiki/File:Bone_cross-section_no_text.svg)，无字版本由 Menglk1988 上传 | 无字长骨剖面，可见骨皮质、松质结构和骨髓腔 | CC BY 3.0 |
| [Commons / Nyq、BDB，原图来自 1918 年 Gray's Anatomy](https://commons.wikimedia.org/wiki/File:Transverse_section_of_bone_en.svg) | 骨组织横断面，标注骨单位、哈弗斯管、骨细胞、骨小管 | CC BY-SA 4.0 |
| [Commons / Kassidy Veasaw](https://commons.wikimedia.org/wiki/File:Cartilage_Structure.jpg) | 软骨层次、软骨细胞和胶原的手绘示意；位图，不是组织切片照片 | CC BY-SA 4.0 |
| [Commons / Hwill016](https://commons.wikimedia.org/wiki/File:Cartoon_Hyaline_Cartilage.jpg) | 透明软骨细胞、陷窝和基质示意；位图 | CC BY-SA 4.0 |
| [Commons / Derek Jansen, Grad Coach](https://commons.wikimedia.org/wiki/File:CONSORT_2010_flow_diagram_for_a_two-group_parallel_randomized_trial.svg) | 两组随机试验的纳入、分配、随访和分析流程，人数均为 `n= ` 空白占位 | CC BY 4.0 |
| [Commons / Derek Jansen, Grad Coach](https://commons.wikimedia.org/wiki/File:Cohort_and_case-control_study_timelines_for_smoking_and_lung_cancer.svg) | 队列随访与病例对照时间线；源页明确 20 年只是示意，不来自特定研究 | CC BY 4.0 |
| [Openclipart / Dustwin，#195847](https://openclipart.org/detail/195847/online-computer-survey) | 蓝白在线问卷界面、选择项与指针；无真实答案或个人信息 | CC0 1.0 |

CONSORT 文件保留 **2010 版**身份；它不是 2025 版指南。队列时间线的示例暴露、结局标签和 20 年时间轴均保留在范围说明中，不可当成用户研究的真实内容或随访时长。Openclipart 图标未描绘具体生物个体，因此 `species` 保持未知，而没有为了检索要求填写物种。

所有七件均已查看官方缩略图。共查看九件候选缩略图，仅下载过一件 12,750 字节的 Openclipart SVG 文本以核对内容版本；没有下载任何整库。缩略图 SHA-256、原件版本 SHA-1/SHA-256、大小、源页、作者和许可保留在清单中。缩略图查看不能代替云端原件校验、预览生成或 Illustrator 原生验收；清单明确记录这些后续检查待完成。

没有纳入的候选：旧版 `ExplainingCaseControlSJW-en.svg` 教学评论较多，采用更清楚的时间线代替；`Health joint.png` 虽在 Commons 标为美国政府作品，但其 NIAMS 原来源未给出逐图作者，而 [NIAMS 的说明](https://www.niams.nih.gov/disclaimer) 区分了外购的受版权保护插画，因此选用两件作者和许可更直接的软骨图。`Generic forest plot.png` 的来源描述包含示例效应值与置信区间，尚未核实为空白模板；森林图空白模板继续保留为真实缺口。

`commons` 仅启用本清单的 6 件，`openclipart` 仅启用 1 件，均为非主力来源，不声称覆盖整站。发现适配器只读已固定 SHA-256 的小清单，不抓取网站或下载图像；原件由现有 GitHub Actions collector 获取。下载时必须匹配文件的固定内容哈希和大小，变化会产生明确失败。原始字节不改写，CC BY-SA 的同许可要求按单件保留。全部七件小于十件，后续应对云端生成的七件预览全部视审。
