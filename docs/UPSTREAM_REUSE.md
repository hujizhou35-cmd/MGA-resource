# 上游复用记录

本项目优先复用已有公开素材、元数据与成熟接口约定。公开源码许可和素材许可分别保留。以下是已核实的借鉴对象，不表示整站或全部源码已复制。

| 项目 | 可复用内容 | 当前采取方式 |
| --- | --- | --- |
| [BioCartoon](https://github.com/josephrich98/biocartoon) | 多源目录、稳定 ID、作者/许可/署名、发布目录、批量下载 | 采用逐项来源与格式/变体分离的目录原则；适配原始图库。其 catalog URL 已有访问拒绝，不重试 |
| [Iconify API](https://github.com/iconify/api) | 一次请求多个素材、缓存和直接获取 | 后续接口将批量返回元数据、预览地址及文件地址；不把大型图片 Base64 填入每次搜索响应 |
| [Bioicons](https://github.com/duerrsimon/bioicons) | 公开 Git 文件树、作者和许可目录 | 直接读取固定 commit 元数据与原图，不重新绘制 |
| [Health Icons](https://github.com/resolvetosavelives/healthicons) | 正式 title/tags/分类与样式元数据 | 从官方目录导入并保留线稿/填色变体关系 |
| [SciGlyph MCP](https://github.com/ZengZichao/SciGlyph-MCP) | 中英名称、画风系列、插画/示意图/图标字段 | 借鉴字段维度；不接入其仅覆盖 sampleAssets 的搜索作为全库搜索 |
| [Morphly Art Packs](https://github.com/SAADAT-Abu/Morphly/blob/main/docs/ADDING_ART_PACKS.md) | 版本素材包、格式/配色变体、SHA-256 与署名清单 | 借鉴可恢复专题包的结构；接受位图，不继承其矢量唯一的范围 |

## 代码复用边界

BioCartoon 数据库/CLI 是 BSD-2-Clause，其编辑器单独为 AGPL。本项目不需要引入大型在线编辑器。Iconify API、Bioicons 网页代码和 SciGlyph MCP 为 MIT，但具体文件的许可与版权说明仍须保留。

若复制或修改上游代码，将在 NOTICE 和对应文件标明原项目、具体版本/commit、文件路径、许可证及修改情况。不能把“参考了接口设计”写成“已运行未经改动的上游工具”，也不能把单件真实验证扩展成整库通过。

已检查到的实现差异：

- BioCartoon 的同步逻辑不能直接作为全面更新策略；已有素材改变时需要通过上游版本或哈希识别。
- SciGlyph MCP 当前搜索样例表，必须扩展到真实完整目录才能承诺全库搜索。
- Iconify 主要服务较小 SVG；医学位图/AI 应保留原件和下载地址。
- Cloudflare 的文件 hash 复用减少上传量，但不是永久备份；完整 Release 素材包与 manifest 承担恢复能力。

所有实际采集、预览、去重和包构建在项目的 GitHub Actions 云端任务中运行。代码和元数据可以在本机开发；整库不经过用户电脑。
