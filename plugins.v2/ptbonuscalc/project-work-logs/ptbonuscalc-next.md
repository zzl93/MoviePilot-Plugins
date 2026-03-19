# PT Bonus Calc – 下一步 Checklist (更新于 2026-03-08)

> 打开本文件即可知道当前可以直接执行的任务；完成后勾选或更新日期。

## 0. 快速进入项目
- 阅读 `project-work-logs/ptbonuscalc.md` 最新一条（2026-03-08），了解状态与上下文。
- 若需要旧实现参考：`D:/project/MoviePilot-Plugins/plugins.v2/ptbonuscalc`。

## 1. 功能对照清单（Due 2026-03-09）
- [x] 对“重构前已完成的 1-5 条”分别定位现仓库文件/模块，并记录 `docs/功能对照-重构v2.md`。
  - 站点做种抓取入库：`MVC/services/site_service.py` + `MVC/utils/page_parser.py`。
  - 下载器抓取入库：`MVC/utils/downloader_fetcher.py` + `MVC/services/downloader_seed_service.py`。
  - `site_seed_id` 关联与保存：`MVC/mappers/downloader_seed_mapper.py` & `bonus_service.apply_save_data`。
  - 站点刷新触发链路：`__init__.py` 事件回调 + `MVC/controller.py` API。
  - 时魔计算：`MVC/utils/bonus.py` + `bonus_service.get_bonus_seeding_data`。
## 2. 解析 & 公式迁移（Due 2026-03-10）
- [x] 参考旧仓库 `server/page_parser.py`、`server/utils.py`，补完 `MVC/utils/page_parser.py` 和 `MVC/utils/bonus.py`。
- [x] 在 `tests/`（若无可建 `tests/parser`）写最少 1 个解析+公式单测，使用 `temp/` 样本或旧 HTML 片段。
- [x] `site_service.sync_site_seeding_data` 成功后能看到 `SiteSeedSnapshot.seed_time`、`bonus_per_hour` 为非零。

## 3. 刷新链路与匹配落库（Due 2026-03-11）
- [ ] 复核 `__init__.py` 的 `_on_site_refreshed`、`controller.py` 的 `/bonus_data`、`/save_data` 实现，确保：
  1. 主项目站点刷新事件触发 `site_service.sync_site_seeding_data`，并串行触发 `downloader_seed_service.sync_downloader_seeds_from_api`（若缺则补）。
  2. Page 页面“刷新”按钮不仅拉站点数据，也能更新下载器候选、bonus params。
  3. `/save_data` 将匹配结果写入 `DownloaderSeed.site_seed_id`，后续刷新可看到“matched”。
- [ ] 用一套 Mock/假数据脚本记录验证步骤，链接回日志“指标/证据”。

## 4. 标签 & 删种策略方案草稿（Due 2026-03-12）
- [ ] 输出 `docs/标签与删种策略设计草案.md`，包含：
  1. 数据字段来源（契约、认领、收藏、上传状态、bonus、空间需求等）。
  2. 标签识别的触发点（站点页面解析 / 下载器 API / 手工标记）。
  3. 删种策略输入输出（释放空间目标、约束、排序指标）、可能算法（贪心/ILP/启发式）。
- [ ] 拟定拆分任务列表（可放在草案末尾），供后续排期。

## 5. 记录 & 沟通
- [ ] 每次推进后更新 `project-work-logs/ptbonuscalc.md`，保持状态颜色与日期。
- [ ] 若创建/更新文档，记得在日志的 “指标 / 证据” 区块添加链接说明。


