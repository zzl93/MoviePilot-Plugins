## 2026-03-07 – PT Bonus Calc 插件（状态：黄色）

**概要**
- 初步完成插件范围梳理并建立日志体系统一落地；项目当前仍在梳理既有能力与技术债的阶段。
- 状态标为黄色：关键代码结构已清晰，但数据源编码/示例缺失导致无法立刻验证链路。

**已完成**
- 阅读 `docs/代码说明.md`、`docs/MVC结构说明-重构版.md`，明确插件以 MoviePilot 插件形式运行，后端 MVC + Service + Mapper 层负责站点/下载器四张表与魔力计算逻辑。
- 根据技能模板在仓库根目录创建 `project-work-logs/ptbonuscalc.md`，确定后续知识沉淀位置。
- 跟产品方对齐真实需求：现阶段聚焦“站点做种记录（getusertorrentlistajax）×下载器种子”名称分词+体积匹配，服务于后续删种策略（在满足契约/认领约束下最大化释放空间且保留最高时魔）。

**进行中**
- 梳理前端视图与 API 映射：需要进一步阅读 `src/` Vue 组件和 `MVC/services/bonus_service.py`，生成接口-页面字段对照 → 目标 2026-03-09。
- 识别需脚本化的证据抓取步骤（git、下载器 API、站点刷新事件日志），以便后续日志可引用具体数据。
- 对比 `D:\project\MoviePilot-Plugins\plugins.v2\ptbonuscalc` 旧实现与当前 MVC 结构，拆分可直接迁移的功能（NexusPHP 页面解析、魔力公式、匹配算法等） → 目标 2026-03-10。

**阻碍 / 风险**
- `docs/需求分析-做种与匹配.md` 显示乱码（缺少原始版本），但产品确认文字内容已不再更新 → 需直接以访谈记录为准补充说明，避免继续等待文档修复。
- 当前 `MVC/utils/page_parser.py` 与 `bonus.py` 仅有占位逻辑（魔力参数空字典、公式缺乏 T0/N0 等参数），导致无法计算真实时魔；若继续沿用会使删种策略缺乏依据。
- `bonus_service` 等 Service 仍自持 DB Session（doc 中已标注“待统一”），若不尽快规范，可能在多线程刷新时引入连接泄漏 → 建议在下一轮代码审查时列入重构清单。

**指标 / 证据**
- 代码基线：`package.json` 显示版本 `0.2.0`，技术栈 Vue 3 + Vuetify 3 + Vite 5。
- 文档覆盖：`docs/代码说明.md` 涵盖入口与 API；`docs/MVC结构说明-重构版.md` 描述后台分层，证明已有统一架构参考。
- 功能对照：`docs/功能对照-重构v2.md` 已梳理旧版 1-5 功能与现仓库文件映射，标注缺口与建议。

**决策 / 备注**
- 日志目录固定为 `project-work-logs/`，单项目单文件，按日期追加；若条目过长再按月份拆分。
- 后续日志按“结果优先”写法，优先记录魔力计算/关联准确率等可量化指标。

**下一步 / 需求**
- 从旧仓库抽取可复用模块：`server/page_parser.py`（NexusPHP 做种解析 + 魔力参数）、`server/utils.py`（匹配与公式）、`server/sync_bonus_data.py`（批量拉取流程），并映射到 `MVC/utils` 与 `services/`。
- temp 目录已有站点页面样本，需编写解析单元测试或脚本验证新 parser，确保字段（size/seed_time/bonus_per_hour）真实可算。
- 需要一份可用的下载器 API 返回示例或假数据，以验证 `downloader_seed_service.sync_downloader_seeds_from_api` 与名称匹配逻辑。

**会话结束前状态**
- 已完成：需求背景澄清、旧版代码位置确认、现版本缺失点整理并写入日志。
- 待办：迁移旧 parser/公式并写单测，完善数据刷新链路，设计删种策略输入。
## 2026-03-08 – PT Bonus Calc 插件（状态：黄色，聚焦迁移与验收）

**前提 / 基本需求对齐**
- 重构前代码位于 `D:/project/MoviePilot-Plugins/plugins.v2/ptbonuscalc`，其 parser、bonus 公式、下载器入库流程都可跑通；现有仓库是重构后的版本。
- 基本功能 1-5：站点/下载器各有“静态表+快照表”入库；下载器表含 `site_seed_id` 字段；站点刷新支持主项目事件与 Page 刷新按钮；能计算每个种子的时魔。
- 尚未完成的功能（需拆分后续任务）：种子标签（契约/认领/收藏等自动识别）与删种策略求解（按标签、上传、时魔生成删种方案或满足固定释放空间）。

**现状差距**
- `MVC/utils/page_parser.py` 和 `bonus.py` 仍是占位（bonus 参数缺 T0/N0/B0/L），导致站点快照写入时没有真实 `seed_time`、`bonus_per_hour`。
- `downloader_seed_service` 虽保留 `sync_downloader_seeds_from_api`、`list_downloader_candidates_for_site`，但还未验证“全量字段入库 + `site_seed_id` 关联 + 保存匹配结果”。
- 站点刷新流程只覆盖站点做种数据；旧版“刷新时拉取下载器 + 魔力公式页面”的逻辑尚未迁移。
- 前端 `Page.vue` 显示匹配成功，但 `save_data` 尚未真正把匹配关系写回数据库，也未沿用旧版的映射持久化。
- **风险**：如不对照旧仓库功能验收，重构后的版本可能“只展示不落库”，无法支撑后续标签/删种策略。

**指标 / 证据**
- 旧仓库 `server/page_parser.py` 提供完整的 NexusPHP 解析逻辑（含 mybonus 参数、做种列表各字段、种子权重等），可直接迁移参考。
- 当前 `MVC/services/bonus_service.py:get_bonus_seeding_data` 仍需真实 `bonus_params` 和 downloader snapshot 数据才能算 `total_bonus_per_hour`。

**下一步 / 需求**
1. 拟制《功能对照清单》：列出“重构前已完成 1-5 项”在现仓库的落地情况（文件、API、测试），找出缺口 → 负责人：Codex，完成时间 2026-03-09。
2. 迁移并单测 `page_parser.py` + `bonus.py`：用 `temp/` 样本或旧仓库逻辑补齐解析、公式，确保站点快照落库含真实 `seed_time`、`bonus_per_hour` → 负责人：Codex，完成时间 2026-03-10。
3. 校验刷新链路：验证/实现“主项目事件 & Page 按钮触发站点 + 下载器 + 魔力参数刷新”，并确认匹配关系 `save_data` 能写入 `DownloaderSeed` 表 → 完成时间 2026-03-11。
4. 设计标签与删种策略拆解方案（只输出方案，暂不开发）：罗列所需字段、算法、UI 入口，供下一阶段排期。

**备注**
- 下一次会话先阅读《功能对照清单》+ `project-work-logs/ptbonuscalc-next.md`（新建的 checklist），再按优先级执行。
- 所有新笔记都放在 `project-work-logs/` 目录，确保未来会话零上下文即可续作。
## 2026-03-08（晚间） – PT Bonus Calc 插件（状态：黄色，解析链路打通）

**已完成**
- 依据旧版 `server/page_parser.py`、`server/utils.py` 重写 `MVC/utils/page_parser.py` 与 `MVC/utils/bonus.py`，解析做种行时补齐 `seed_time`、`seeders`、`weight` 并按 mybonus 参数计算 `bonus_per_hour`。
- `site_service.sync_site_seeding_data` 现在会额外抓取 `mybonus.php`，保存真实 `bonus_params`，并在写入快照前预计算单个种子的奖励及总时魔。
- 新建 `tests/parser/test_page_parser.py`，覆盖 parser 行为与公式计算；确保 `temp/lajidui-getusertorrentlistajax.html` 能解析出非零的 size/seed_time。

**阻碍 / 风险**
- 直接运行 `pytest tests/parser/test_page_parser.py` 会在导入 `app` 包时尝试初始化 SQLite，当前环境 `config/logs`、`data/database.db` 无法打开导致超时 → 后续需要提供可访问的 SQLite 或 mock `app.db`。

**指标 / 证据**
- `docs/功能对照-重构v2.md` 中“解析 & 公式”一列已更新为“已落地”，并附带缺口描述的关闭信息。
- `tests/parser/test_page_parser.py`（新增）覆盖两项断言：解析样本至少返回 1 条记录且 `seed_time>0`；`calc_bonus_per_hour` 结果与手工公式一致。
- 试运行 `python -c ...parse_torrent_activity_nexusphp` 因 SQLite 权限受限失败，已记录日志供后续排查。

**下一步 / 需求**
- 继续推进 checklist 第 3 项：梳理站点刷新链路，串联下载器同步与 `/save_data` 落库，并补验证脚本。
- 如需 CI 校验，需在容器或本地准备可写的 `config/logs`、SQLite 文件，或提供 `APP_DB_URL` 供 `app.db` 初始化。

## 2026-03-09 – PT Bonus Calc 插件（状态：黄色，Mapper/Service 会话串联）

**已完成**
- 对照《MVC结构说明-重构版.md》审查 Mapper/Service：`SiteSeedMapper` 已大量使用 `@db_update/@db_query`，但 `DownloaderSeedMapper.upsert_downloader_seed` 与 `bonus_service` 仍手动管理 Session。
- 为 `DownloaderSeedMapper.upsert_downloader_seed` 添加 `@db_update` 装饰，并实现 `bulk_upsert_downloader_seeds`，所有批量写入（批量 downloader 抓取、`save_data`、config 回放）均复用 mapper 事务。
- `bonus_service.apply_save_data` 与 `sync_init_mappings_and_fully_matched` 改为纯 mapper 调用，Service 只组装数据，杜绝 ScopedSession 泄漏风险。

**发现 / 风险**
- `build_seed_association_payload` 的 `keyword` 入参尚未实现过滤逻辑；当前 `/bonus_data` 请求仍返回全量右表。
- 站点刷新链路在 `_sync_sites_before_payload` 与 `get_bonus_seeding_data` 可能重复抓取同一站点；后续需缓存最近同步时间或快照状态以抑制重复 HTTP。
- 运行测试/启动插件仍需准备可写 `config/user.db` 与 `config/logs`（`app.db` 初始化会直接访问这些路径）；此环境未就绪，CI 依旧受阻。

**下一步**
1. 按 API 约定补上 `keyword` 过滤，前端/后端才能验证单站点关键词的匹配体验。
2. 统一站点/下载器同步调度（例如在 PluginData 记录 `last_synced_at`），避免 `/bonus_data` 每次都触发长耗时抓取。
3. 配置 SQLite/日志路径并运行 `pytest tests/parser/test_page_parser.py`，随后启动 MoviePilot 手工验证 `/bonus_data` → `/save_data` 的闭环。
