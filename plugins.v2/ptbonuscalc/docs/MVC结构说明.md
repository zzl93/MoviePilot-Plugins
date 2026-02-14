# PT 魔力计算器插件 - MVC 结构说明

按 MVC 概念重新划分后的文件与方法归属。**所有 MVC 相关实现均在 `server/` 下**，插件入口 `__init__.py` 只做调用，不承载业务逻辑。

---

## 1. 插件入口 `__init__.py`（仅调用，无转发方法）

- 保留插件基类必须的接口：`get_state`、`stop_service`、`get_command`、`get_render_mode`、`get_form`、`get_page`、`get_api`。
- **不在 __init__.py 中定义** `_api_form_options`、`_on_site_refreshed`、`_api_bonus_data` 等转发方法；直接使用 server 层方法。
- `init_plugin`：初始化 DB、从配置解析并设置类属性（如 `sync_downloaders`、`selected_sites`、`site_address_mappings`），注册站点刷新事件时**直接传入** `server.controller.on_site_refreshed`（通过 lambda 绑定 `plugin` 与 `event`），然后**直接调用** `server.controller.init_plugin(plugin, config)`。
- `get_api`：返回的路由表中，`endpoint` 直接指向 server 层方法（通过 lambda 绑定 `plugin`），例如：
  - `/form_options` → `lambda: server.controller.api_form_options(plugin)`
  - `/downloader_tracker_options` → `lambda data: server.controller.api_downloader_tracker_options(plugin, data)`
  - `/bonus_data` → `lambda data: server.controller.api_bonus_data(plugin, data)`
  - `/save_data` → `lambda data: server.controller.api_save_data(plugin, data)`
- Controller 内部如需 `get_sites_to_query`、`get_bonus_seeding_data`、`save_torrent_mapping` 等，由 `server.controller` 内直接调用自身或 Model，不经过 __init__.py。

---

## 2. Controller 层（`server/` 下）

**新建** `server/controller.py`，负责接收请求/事件、调用 Model、组装返回给前端的数据。

| 方法 | 功能 |
|------|------|
| `init_plugin(plugin, config)` | 站点信息写入 PluginData、配置关联与自动匹配同步、站点全匹配标识 |
| `api_form_options(plugin)` | 配置页：站点、下载器、地址关键词选项等 |
| `api_downloader_tracker_options(plugin, data)` | 按下载器返回 Tracker 地址选项 |
| `on_site_refreshed(plugin, event)` | 站点刷新事件：触发同步做种数据 |
| `get_sites_to_query(plugin, site_id, filter_by_selected_sites)` | 按站点 ID/勾选站点查主项目站点列表 |
| `get_sites_to_query_from_plugindata(plugin, site_id)` | 仅从 plugindata 取站点列表（Page 用） |
| `get_latest_userdata(plugin, domain_key)` | 按域名取最新用户数据 |
| `get_bonus_seeding_data(plugin, site_id, use_plugindata_only)` | 做种数据：站点块 + 魔力计算，可拉下载器并写表 |
| `api_bonus_data(plugin, data)` | 做种与关联数据 API：未匹配列表、下载器候选等 |
| `downloader_torrents_list(plugin, keyword)` | 下载器种子列表（供配置等） |
| `get_torrent_mappings(plugin)` | 站点种子与下载器 hash 的关联映射 |
| `save_torrent_mapping(plugin, site_domain, torrent_key, downloader_hash, ...)` | 保存一条站点种子与下载器种子关联 |
| `remove_torrent_mapping(plugin, site_domain, torrent_key)` | 取消一条关联 |
| `build_seed_association_payload(plugin, override_config, site_id, keyword)` | 构建关联页 payload：未匹配、options、按站点下载器候选 |
| `api_save_data(plugin, data)` | 保存/删除关联 API 逻辑 |

Controller 通过 `plugin` 访问配置与存储（如 `plugin.get_data`、`plugin.save_data`、`plugin.sync_downloaders`、`plugin.selected_sites` 等），业务数据与持久化全部通过 Model 层完成。

---

## 3. Model 层（`server/` 下，保持现有文件）

数据、持久化与领域逻辑，不处理 HTTP/事件，仅被 Controller 或其它 Model 模块调用。

### `server/utils.py`
Tracker 解析、魔力公式、配置解析、名称分词与匹配、显示宽度等工具。

| 方法 | 功能 |
|------|------|
| `parse_tracker_domain` | 从 tracker URL 解析出域名 |
| `tracker_full_host` | 取 tracker 完整主机名（小写） |
| `keyword_to_domain` | 配置关键词转统一域名 |
| `tracker_domain_group_key` | 从 tracker 取分组键 |
| `build_torrents_by_tracker_domain` | 下载器种子按 tracker 域名分组 |
| `parse_pubdate_weeks` | 发布时间转距今周数 |
| `calc_bonus_per_hour` | NexusPHP 魔力公式 (B, A, A/GB) |
| `parse_list_config` | 配置项解析 |
| `torrent_key` | 种子唯一键 |
| `display_width` / `truncate_by_display_width` | 显示宽度与截断 |
| `tokenize_name_for_match` / `token_overlap_score` | 名称分词与重叠度 |
| `match_torrent_core` | 站点与下载器种子匹配 |
| `format_seeding_time` | 做种时间格式化 |
| `get_candidate_torrents_for_site` | 按站点地址映射筛选下载器种子 |

### `server/seedinfo_oper.py`
四张表（站点种子主/快照、下载器种子主/快照）的数据库操作与关联业务。

| 方法 | 功能 |
|------|------|
| `init_seedinfo_db` | 建表并做表结构迁移 |
| `_migrate_schema` / `_migrate_table` | 表结构迁移 |
| `_normalize_downloader_state` | 下载器状态标准化 |
| `batch_save_seeding_from_parser` | 解析结果批量写入站点主表+快照 |
| `list_site_seeds_with_latest_snapshot_by_site` | 按站点查站点种子及最新快照 |
| `delete_seed_info_by_site` | 按站点删除站点种子相关数据 |
| `get_site_seed` | 按站点+种子键查站点种子 |
| `_get_latest_site_snapshot` | 站点种子最新快照 |
| `_get_downloader_seed_by_site_seed_id` | 按 site_seed_id 查关联下载器种子 |
| `get_downloader_seed_by_hash` | 按 hash 查下载器种子及最新快照 |
| `_get_latest_downloader_snapshot` | 下载器种子最新快照 |
| `upsert_downloader_seed` | 写入/更新下载器种子主表+当日快照 |
| `clear_downloader_seed_site_seed_id` | 取消站点种子关联 |
| `list_downloader_seeds_with_latest_snapshot` | 按下载器名查所有下载器种子及最新快照 |
| `batch_upsert_downloader_torrents` | 批量写入/更新下载器种子主表+快照 |
| `list_seed_with_latest_snapshot_by_site` | 按站点查站点+下载器联合列表 |
| `get_seed_with_latest_snapshot` | 按站点+种子键查一条站点+下载器四元组 |
| `save_seed_info` | 写站点/下载器主表与快照并设关联 |
| `remove_seed_info` | 取消关联 |
| `save_torrent_mapping` | 补全 sd/dd、seed_attr 后调用 save_seed_info |
| `list_all_mappings` | 返回站点种子→下载器 hash 映射表 |
| `apply_downloader_to_row` | 用下载器数据填充展示行 downloader_* 字段 |

### `server/downloader_fetcher.py`
从 qBittorrent 拉取种子列表。

| 方法 | 功能 |
|------|------|
| `get_qb_instance` | 获取 qBittorrent 下载器实例 |
| `fetch_downloader_torrents` | 从配置的下载器拉取种子列表 |
| `fetch_downloader_torrents_by_domain` | 拉取后按 tracker 域名分组返回 |

### `server/sync_bonus_data.py`
做种与魔力参数同步：从站点用户数据或页面拉取并写入插件存储。

| 方法 | 功能 |
|------|------|
| `_fetch_page` | 请求页面返回 HTML |
| `sync_from_fetch` | 主动拉取 NexusPHP 做种页并写入插件表与 PluginData |

### `server/page_parser.py`
NexusPHP 页面解析：做种列表与魔力参数。

| 方法 | 功能 |
|------|------|
| `_prepare_html_text` | 预处理 HTML 干扰字符 |
| `parse_bonus_params_nexusphp` | 解析 mybonus 页魔力参数 |
| `parse_torrent_activity_nexusphp` | 解析做种页，返回做种列表及下页地址 |
| `extract_userid_from_index_nexusphp` | 从首页解析 userid |

### `server/models/seedinfo.py`、`server/models/__init__.py`
四张表 ORM 模型：`SiteSeed`、`SiteSeedSnapshot`、`DownloaderSeed`、`DownloaderSeedSnapshot`；`__init__.py` 导出上述四类。

---

## 4. View 层

配置页与详情页由 **Vue** 组件渲染（Config.vue、Page.vue）。数据通过 Controller 提供的 API 获取或提交：`/form_options`、`/downloader_tracker_options`、`/bonus_data`、`/save_data`。View 不放在 `server/` 下，仅在此说明数据来源。

---

## 5. 目录与调用关系小结

```
app/plugins/ptbonuscalc/
├── __init__.py               # 仅：插件接口 + 调用 server.controller / server.*
└── server/
    ├── controller.py         # 【新建】Controller：请求/事件 → 调 Model → 返回数据
    ├── utils.py              # Model：工具
    ├── seedinfo_oper.py      # Model：持久化与关联
    ├── downloader_fetcher.py # Model：下载器拉取
    ├── sync_bonus_data.py   # Model：同步做种与魔力参数
    ├── page_parser.py       # Model：页面解析
    └── models/
        ├── __init__.py
        └── seedinfo.py      # Model：ORM 实体
```

- **__init__.py**：不实现业务、不定义转发方法；`get_api` 的路由 endpoint 直接绑定 `server.controller.*`（lambda 传入 plugin），事件监听直接绑定 `server.controller.on_site_refreshed`，`init_plugin` 内直接调用 `server.controller.init_plugin` 与 `server.seedinfo_oper.init_seedinfo_db` 等。
- **Controller**：仅 `server/controller.py`，入参统一带 `plugin`，通过 plugin 读配置与存储，通过 Model 读写在库与外部服务。
- **Model**：上述 `server/` 下除 `controller.py` 外的所有模块与方法。
