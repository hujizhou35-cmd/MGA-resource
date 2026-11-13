# 免费网站实施方案

组件正式报告 `component-43481252923.json` 已通过，网站仍需单独线上验收。

主站 `mga-resource-mainly-for-agent.pages.dev` 使用 Pages Functions + D1，直接导入已验收的 `search-core.mjs`。当前 OAuth 已有 Pages/D1 权限；无需额外 Workers 权限。Agent 可通过 JSON 搜索接口获得选中文件的直接预览、原件、SHA-256、许可、来源及用途不确定性。结构化过滤、分页、种子与项目选用记录保留同一检索规则。

原件和预览按内容哈希分布到三个独立 Pages 静态站点。单件达到25 MiB时使用 GitHub Release 中的独立原件，不要求 Agent 解压整包。完整来源元数据按家族作为主站静态JSON发布。云端准备SQL和静态文件，本地只保留代码与有界验收证据。

2026-11-14核对的官方约束：Pages Free每站20,000文件、单件25 MiB；Functions共用Workers Free的100,000请求/日和10ms CPU；D1每库500MB、账户5GB、5百万读行/日及10万写行/日。使用Free计划，不添加付费产品，不升级额度；超限报错或等待重置。静态图片请求不走检索函数。SQL仅保留用于同一排名的逐文件变体和FTS，省去重复家族表及无用索引；必须核实际导入写行数和线上消耗。

官方依据：[Pages限制](https://developers.cloudflare.com/pages/platform/limits/)、[Workers限制](https://developers.cloudflare.com/workers/platform/limits/)、[D1限制](https://developers.cloudflare.com/d1/platform/limits/)、[D1价格](https://developers.cloudflare.com/d1/platform/pricing/)。

人类页采用医学素材抽屉布局：上方检索、左侧少量真实过滤、右侧大图与署名。蓝灰底 `#edf3f7`、白色样本区 `#ffffff`、深蓝字 `#17364a`、青色操作 `#087c8a`、琥珀提示 `#956016`。标题使用系统衬线Georgia，正文系统无衬线，API样例等宽。视觉重点是可查看的医学对象，而非大幅营销图；同一结果可以切换素材图与Agent JSON。无装饰动画，支持键盘与窄屏。

部署通过条件：同一请求选中文件/来源/顺序与已验收原始结果一致；静态发布文件清单与19473原件、38748对象一致；直接下载实际字节及哈希通过；排除项不出现在搜索、目录、下载中；实际浏览和Agent完整查询-预览-下载操作通过。组件验收或CI不能替代这些条件。
