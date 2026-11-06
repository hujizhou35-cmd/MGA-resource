# 免费托管方案与容量准备（2026-11-07）

当前只完成架构核查和冻结元数据的容量测量。用户范围决定、素材验收和网站验收仍未完成；没有开始建站、创建云资源、导入远端 D1 或开通收费产品。本文件不改变 `closed_pending_collection_and_acceptance` 状态。

## 当前冻结版本的实测容量

测量依据为 `build/final-scope-baseline/assembly-42289434868-1.*`，公开目录 SHA-256 为 `5c90306415ce5e7f36e875c7f57b19a91930aacfeeaeecdf9ad7662770f9a3fe`；schema/index 9、shared-search 3.4.1、appearance 1.1.0。目录、summary、scope report、assembly report 和 SQLite 的目录版本哈希相符。完整机器结果见 [本次容量诊断](../data/diagnostics/free-hosting-capacity-20261107-index9.json)。

| 项目 | 本次测量 |
| --- | ---: |
| 逻辑家族 | 7,839 |
| 原始 SQLite | 403,369,984 B |
| `file_variants` 行数 | 20,130，其中 20,129 eligible |
| 最小 D1 投影 | 160,133,120 B，152.715 MiB |
| 投影 gzip（level 6，mtime 0） | 12,914,303 B，12.316 MiB |
| 去重原件 | 19,474 个，4,165,979,500 B |
| 按 SHA-256 去重的预览 | 19,275 个，975,915,983 B |
| 现有预览路径 | 19,473 个 |
| 原件加去重预览 | 38,749 个静态条目，尚未计入页面、目录和发布清单 |

投影只保留 `file_variants`、`index_metadata`、`file_variants_fts`，以及 `variants_family`、`variants_file` 索引，包含主键索引和 FTS 内部表；移除旧的 `assets`、`assets_fts`、`asset_taxonomy`、`asset_formats`。各表行数与原数据库一致，内存投影 `PRAGMA quick_check` 为 `ok`。投影数据库和 gzip 只存在于内存，没有额外写出大文件。

之前历史 index 6/shared-search 3.3.0 的投影为 130,822,144 B，gzip 为 11,421,876 B，仍作为历史记录保留。它们不是本次 scope 和 appearance 版本的容量。

三个原 SVG 超过拟定的 24 MiB 余量，也超过平台 25 MiB 单文件上限：

| 素材 | 来源 | 原件大小 |
| --- | --- | ---: |
| Extracellular matrix (ECM) | Togo | 31,405,223 B |
| Lipid Bilayer | SciDraw | 29,020,348 B |
| Structure of skeletal muscle | Togo | 28,826,010 B |

这些原件继续保留在现有云端归档。后续发布阶段优先评估少量单件 GitHub Release asset 直链：Agent 对二进制下载地址发起 GET 并自动跟随 HTTP 重定向，无需打开来源网页或解压整个素材归档；目前尚未生成这些单件发布附件。也可以另行验证 `.svg.gz` 无损下载，必须在云端核对大小及恢复后的原件哈希。本次没有生成压缩副本，没有验收 `Content-Encoding` 或解压实现，也没有证明它们可以作为裸 SVG 上传 Pages。所有预览均未超过 24 MiB。另有一个 SciDraw `DNA` 原件缺失成功预览，不能把本次容量测量解释为预览或素材验收全部通过。

## 两种可用的 Cloudflare 方案

| Free 套餐项目 | Pages + Functions | Workers + Static Assets |
| --- | --- | --- |
| 动态请求 | 与 Workers 合计 100,000 次/日 | 同一账户共享额度 |
| 动态 CPU | 10 ms/请求 | 10 ms/请求 |
| 静态请求 | 不调用 Functions 时免费、无限 | 不调用 Worker 时免费、无限 |
| 静态文件数 | 每站 20,000 个 | 每 Worker 版本 20,000 个 |
| 单个静态文件 | 25 MiB | 25 MiB |
| 项目数 | 每账户 100 个 Pages 项目 | Free 每账户 100 个 Workers |
| D1 | 同一 D1 产品和额度 | 同一 D1 产品和额度 |

来源：[Pages 计费](https://developers.cloudflare.com/pages/functions/pricing/)、[Pages 限制](https://developers.cloudflare.com/pages/platform/limits/)、[Workers 限制](https://developers.cloudflare.com/workers/platform/limits/)、[Workers 静态资产计费](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)。Pages 概览将 Free 限制表述为每月 500 次部署，规划中保守计入该预算，不因 GitHub Actions 自行构建就假定部署次数无限。[Pages 概览](https://developers.cloudflare.com/pages/)

目前可以把 Pages-only 作为较少配置的可实施选项。Cloudflare 官方优先推荐新项目使用 Workers；这里选择 Pages 的理由是现有权限和当前功能需求相符，并非它有更高的免费额度。若之后采用 Workers，目录格式、共享检索核心和素材分片仍可复用。

建议的布局是一个 Pages 主站承载人工页面、静态 catalog 和 `/api`，再以纯静态 Pages 项目承载原件与预览。站名可为 `mga-resource-mainly-for-agent.pages.dev`，素材分片为 `mga-resource-assets-01.pages.dev` 等；这些只是拟定名称，未查询占用或创建。API 返回本库分片的直接文件 URL，下载无需回到上游来源网站，也无需 Functions 代理大文件。

以每片最多 18,000 个文件计算，本次条目数至少需要 3 个素材分片；剔除三个超大原件后仍至少 3 片。最终清单还要计入生成文件，并结合云 runner 的磁盘预算分片。不能把下限计算当作实际发布清单已经验证通过。

## Pages 权限核查

以下官方接口均明确接受 `Pages Write`：

- [创建 Pages 项目](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/create/)。
- [更新项目](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/edit/)，包括 `deployment_configs.production.d1_databases`。
- [获取 Direct Upload 的短期上传 JWT](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/get_upload_token/)。
- [创建 deployment](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/create/)，同一请求支持 `_worker.js`、`_worker.bundle` 和 `_routes.json`。

因此，Pages Functions 和 D1 绑定不因底层使用 Workers runtime 就另需 Workers Write。D1 数据库的创建和导入属于 D1 管理操作，使用相应 D1 权限。官方 [Pages D1 bindings](https://developers.cloudflare.com/pages/functions/bindings/#d1-databases) 和 [Wrangler 配置](https://developers.cloudflare.com/pages/functions/wrangler-configuration/)也支持此组合。

本次只读实查：Wrangler 4.151.0 的 `whoami` 显示已授予 `pages(write)`、`d1(write)`；Workers 权限在缺失 scope 警告中。`wrangler pages project list` 成功。Wrangler 部署实现将 Functions bundle 提交到 Pages deployment 接口。没有执行写权限试探，没有获取上传 JWT，没有部署；所以结论是官方权限模型匹配，而不是当前账户已完成部署实测。

## 静态文件必须绕过 Functions

使用 advanced `_worker.js` 时，显式限定调用范围：

```json
{"version":1,"include":["/api","/api/*"],"exclude":[]}
```

该 `_routes.json` 放在输出目录。只有 `/api` 和其子路径进入 Functions；页面、预览、原件、catalog 和说明文件保持静态。仅在函数内部调用 `env.ASSETS.fetch()` 不会取消已经发生的动态调用。[Functions 路由](https://developers.cloudflare.com/pages/functions/routing/)、[Advanced mode](https://developers.cloudflare.com/pages/functions/advanced-mode/)

静态 catalog 是可恢复的基础，API 是便捷入口。API 请求额度、CPU 或 D1 额度不足时，Agent 可以下载压缩元数据，在自己的运行环境使用同一检索核心；公开静态文件不应依赖 API 代理。Pages 的 fail-open 可以处理日 Functions 额度耗尽，但不能把 CPU、D1 错误或浏览器检索能力也视为自动解决。12.316 MiB 的 gzip 只证明这个冻结投影适合单个静态下载文件，不证明浏览器内存占用或客户端检索已经验收。

## 云端 Direct Upload 与尚未完成的验证

沿用公共 GitHub Actions：从云端 Release 取回需要的分片到 runner 临时盘，按冻结 scope 生成清单，检查大小和 SHA-256，运行 `wrangler pages deploy`，完成后清理该片。原图不经过用户本机，但仍存在云端下载和上传流量。Wrangler 的 Pages 上传会查询缺失 hash 并复用可用的已上传内容；每次 deployment 仍必须提交完整的目标 manifest，不能只把新增文件目录当作全量发布。

官方 [Direct Upload CI 指南](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)要求 Pages Edit API token，并支持预构建目录。当前 GitHub repository secrets 列表和 environments 均为空，CI 凭据尚未配置。本机 OAuth 登录不能当作 GitHub Actions 已获授权；后续部署阶段配置 Pages Edit，若该工作流也导入 D1 再配置 D1 Edit，无需为 Pages-only 补 Workers Edit。本次没有创建或复制凭据。

D1 Free 每库 500 MB、每天 500 万行读取和 10 万行写入；索引会增加写入计数。152.715 MiB 投影低于存储上限，但 20,130 条变体加主键、普通索引及 FTS 的首次导入，不能据此保证一天内完成。[D1 价格](https://developers.cloudflare.com/d1/platform/pricing/)、[D1 限制](https://developers.cloudflare.com/d1/platform/limits/)、[索引与计费](https://developers.cloudflare.com/d1/best-practices/use-indexes/)

部署阶段应先用小批量读取远端 `meta.rows_written`、`meta.rows_read` 和数据库大小，再确定分批导入预算与恢复点；必要时跨 UTC 午夜分日完成，保留每日余量和账户其他用量。FTS/JSON 筛选、计数与宽查询必须单独测量，返回少量结果不代表只读取少量行。远端 CPU 也要实际测量；本机 wall time 不是 Cloudflare CPU，SQLite `total_changes` 不是 D1 计费行数。[D1 返回 metadata](https://developers.cloudflare.com/d1/worker-api/return-object/)、[Workers CPU 定义](https://developers.cloudflare.com/workers/platform/limits/#cpu-time)

仍未验证的事项：当前账户实际是否为 Free 套餐；远端部署权限最终写入结果；CI 凭据；D1 导入和查询实际读写量；远端 CPU；最终静态发布清单与三个超大件交付；用户范围和素材验收。以上准备不启动网站建设，也不声称线上吞吐已达标。
# 采集归档附件上限的实际记录

2026-11-07，`collection-20261106` 达到 GitHub Release 的附件数量上限；运行 `42298772894` 上传外部汇总 JSON 时收到 `file_count limited to 1000 assets per release`。同次 13,430,022 字节的冻结检索预览包已成功上传，经 GitHub digest、本地字节和 60 张联系表逐项哈希核验可用，不能把该运行的失败状态改写为成功。

旧归档及其引用保留，后续验收证据使用 `acceptance-20261107`。云端读取来源归档的 tag 与输出证据的 tag 分开；不为腾位置删除旧来源，也不把所有后续文件塞进单一 Release。恢复记录见 `data/diagnostics/frozen-review-42298772894-artifact-recovery.json`。本次仅下载有界预览包和元数据，没有下载原图库，也未开始网站部署。

后续预览审阅命令已进一步区分三处位置：`--release-tag` 读取指定目录或来源记录；可重复的 `--archive-release-tag` 补充原件／预览归档证据；`--publish-tag` 仅决定新审阅包保存位置。迁移目录不重写图像的归档 URL。不同 Release 中同名附件以完整 URL 区分，不能按文件名互相覆盖。相关 workflow 的输出默认采用 `acceptance-20261107`，包括普通检索审阅和冻结结果复核两个分支。

本轮通过48项有界测试，覆盖原有预览提取、范围提名、精确哈希绑定，以及新目录、旧归档、新输出分开时的调用行为。另外只读访问真实 GitHub 元数据，成功从新验收 Release 读取5个附件，并合并127条归档证据（旧归档126条、新验收包1条）；没有因此重新下载原件。此结果证明元数据跨归档读取可用，不表示已经重新运行全库装配、打包、独立恢复或发布验收。

