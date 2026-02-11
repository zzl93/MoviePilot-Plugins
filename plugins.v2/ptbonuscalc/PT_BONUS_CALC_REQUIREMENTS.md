# PT魔力计算器插件 - 逻辑需求分步文档

> 依据 `C:\tool\project\MoviePilot-Plugins\plugins.v2\ptbonuscalc\__init__.py` 整理

---

## 1. 插件概述

| 项目 | 内容 |
|------|------|
| 插件名称 | PT魔力计算器 |
| 渲染模式 | Vuetify（schema 拼装） |
| 核心功能 | 展示主程序 PT 魔力计算所需信息；提供做种魔力列表；站点种子与下载器种子关联；每小时魔力公式计算 |

---

## 2. 配置与初始化

### 2.1 配置项

| 配置键 | 说明 | 类型 |
|--------|------|------|
| `selected_sites` | 显示站点，留空显示所有启用站点 | 多选，域名列表 |
| `sync_downloaders` | 同步数据下载器，从这些 qBittorrent 拉取种子用于关联 | 多选，下载器名称 |
| `site_address_mapping_{domain}` | 站点 → 下载器地址关键词映射 | 多选，每站点一组 |
| `torrent_mapping_{site}_{torrent_key}` | 站点种子 → 下载器 hash 手动关联 | 下拉选择，表单级 |

### 2.2 init_plugin 流程

1. 解析 `sync_downloaders`、`selected_sites`
2. 解析 `site_address_mapping_*` → `site_address_mappings`
3. 调用 `_get_bonus_seeding_data()` 获取自动匹配
4. 合并表单中的 `torrent_mapping_*`（手动优先），写入 plugindata `torrent_mappings`
5. 计算全匹配站点，写入 plugindata `site_fully_matched`

---

## 3. 魔力公式

### 3.1 NexusPHP 公式

```
B = B0 * (2/π) * arctan(A/L)
A = c1 * S_GB * c2 * weight
c1 = 1 - 10^(-T_weeks/T0)
c2 = 1 + √2 * 10^(-(N-1)/(N0-1))
```

- `T_weeks`：发布时间距今周数
- `S_GB`：种子大小（GB）
- `N`：做种人数
- `T0/N0/B0/L`：来自 `bonus_params`（站点 mybonus 解析）
- `weight`：做种项 `weight`/`wi` 或 `default_weight`

### 3.2 总魔力

- 总 A = Σ 各种子 A，保留一位小数
- 总 B = B0*(2/π)*arctan(总A/L)
- 做种数奖励：`seeding_bonus_per_seed * min(做种数, seeding_bonus_cap)`
- 小数位按 `hourly_bonus_decimals` 截断

---

## 4. 数据流

### 4.1 做种数据来源

- `SiteOper.get_userdata_by_domain(domain)` 取最新用户数据
- `userdata.torrent_activity.seeding` → 做种列表
- `userdata.bonus_params` → T0/N0/B0/L、做种数奖励等
- `pubdate` → `_parse_pubdate_weeks()` 解析周数

### 4.2 下载器数据

- `ModuleManager` → `DownloaderType.Qbittorrent` 获取实例
- `qb.get_torrents()` 拉取种子
- 按 `tracker` 解析域名 → `_build_torrents_by_tracker_domain()`

### 4.3 站点种子 ↔ 下载器种子 匹配

- 仅当配置了 `site_address_mappings` 时匹配
- `_get_candidate_torrents_for_site()`：按站点地址关键词筛选下载器种子
- `_match_torrent()`：
  1. 优先查 plugindata 手动映射
  2. 大小 ±1% + 名称相似度 ≥0.5
- 映射 key：`{site_domain}|{torrent_id}` 或 `{site_domain}|{name}|{size}`

---

## 5. 表单（get_form）

### 5.1 主表单

- 显示站点：VSelect 多选，可选空
- 同步数据下载器：VSelect 多选（仅 qbittorrent）
- 按钮：「打开站点地址映射与种子关联管理」→ 打开 VDialog

### 5.2 弹窗内容

1. **站点地址映射**
   - 只展示「显示站点」已选且有配置数据的站点
   - 每站：VSelect 多选「下载器地址关键词」
   - 选项：tracker 域名 + 已配置关键词，规范为域名

2. **种子关联管理**
   - 仅展示未匹配的站点种子
   - 全匹配站点不参与
   - 未选「显示站点」→ unmatched 为空
   - 按站点折叠：VExpansionPanel
   - 每行：种子名、大小、下拉选下载器种子（options 按地址关键词筛选）
   - 保存后关联写入 plugindata

### 5.3 默认值

- `site_address_mapping_{domain}`：从 `site_address_mappings` 规范化
- `torrent_mapping_{site}_{torrent_key}`：从 plugindata `torrent_mappings` 读取

---

## 6. 详情页（get_page）

### 6.1 前置条件

- 未选显示站点 → 提示「请先在插件配置中勾选「显示站点」」
- 无做种数据 → 提示「暂无做种数据或未配置站点...」

### 6.2 展示结构

- 按站点 VExpansionPanel 折叠
- 每站：做种数、每小时总魔力、魔力参数是否齐全
- 表格列：序号、种子名、大小、做种人数、发布时间、T(周)、A值、A/GB、每小时魔力、关联状态、分享率、上传量、做种时长
- 种子按每小时魔力降序

---

## 7. API 列表

| 路径 | 方法 | 说明 |
|------|------|------|
| `/test_main_data` | GET | 测试主程序数据：用户做种、站点适配、parser 解析网页 |
| `/raw_seeding_info` | GET | 原始 seeding_info（未整理） |
| `/bonus_seeding_list` | GET | 做种魔力列表，支持 site_id |
| `/save_torrent_mapping` | POST | 保存种子关联 |
| `/remove_torrent_mapping` | POST | 删除种子关联 |
| `/unmatched_torrents` | GET | 未匹配站点种子 |
| `/downloader_torrents` | GET | 下载器种子列表，可 keyword 筛选 |
| `/associate_torrent` | POST | 关联种子（可选返回下载器种子列表供选择） |
| `/remove_associate` | POST | 取消关联 |
| `/seed_association_data` | POST | 用当前或传入配置刷新未匹配+下拉候选（不保存即可刷新） |

---

## 8. 辅助函数

| 函数 | 用途 |
|------|------|
| `_parse_pubdate_weeks` | 发布时间 → 距今周数 |
| `_calc_bonus_per_hour` | 单种子每小时魔力、A、A/GB |
| `_get_parser_parse_info` | 根据 schema 取 parser 解析页信息 |
| `_parse_list_config` | list/逗号字符串 → 非空列表 |
| `_torrent_key` | torrent_id 或 name\|size 生成唯一键 |
| `_display_width` | 字符串显示宽度（中文=2） |
| `_truncate_by_display_width` | 按显示宽度截断 |
| `_keyword_to_domain` | 关键词/URL → 规范域名 |
| `_normalize_name` | 标准化名称，便于匹配 |
| `_name_similarity` | 名称相似度 0–1 |
| `_format_seeding_time` | 秒 → 可读做种时长 |

---

## 9. 依赖

- `SiteChain`、`SitesHelper`、`SiteOper`
- `ModuleHelper`、`ModuleManager`
- `ServiceConfigHelper`、`DownloaderType`
- `StringUtils`
- `fastapi.Query`、`fastapi.Body`

---

## 10. Vue 模式实现指南

### 10.1 架构分工

| 层 | 职责 |
|----|------|
| Python | 配置解析、魔力计算、匹配逻辑、数据持久化、提供 API |
| Vue | 配置表单 UI、详情页展示、与用户交互、调用 API |

### 10.2 新增 API（Vue 用）

| 路径 | 方法 | 说明 |
|------|------|------|
| `/form_options` | GET | 返回表单选项：sites、downloaders、address_keyword_options，供 Config 下拉用 |

### 10.3 Config.vue 实现步骤

1. **挂载**：从 `initialConfig` 取已保存配置，调用 `form_options` 取站点/下载器/地址关键词选项。
2. **主表单区**：
   - VSelect 多选「显示站点」，items 来自 form_options.sites
   - VSelect 多选「同步数据下载器」，items 来自 form_options.downloaders
3. **弹窗**：「打开站点地址映射与种子关联管理」
   - 站点地址映射：仅展示「显示站点」已选且有配置数据的站点；每站 VSelect 多选「下载器地址关键词」，items 来自 form_options.address_keyword_options
   - 种子关联：调用 `POST seed_association_data`，传入当前 form 值（支持未保存即刷新），得到 unmatched_by_site、每行 options
4. **保存**：`emit('save', config)`，config 为扁平对象：
   - `selected_sites`、`sync_downloaders`
   - `site_address_mapping_{domain}`（每站）
   - `torrent_mapping_{site}_{torrent_key}`（每行）
5. **主应用**：收到 save 后调用 `PUT /plugin/{id}`，传入 config，后端 `init_plugin` 处理。

### 10.4 Page.vue 实现步骤

1. 挂载调用 `GET bonus_seeding_list`
2. 无 selected_sites → 提示「请先配置显示站点」
3. 无数据 → 提示「暂无做种数据...」
4. 有数据：按站点 VExpansionPanel，每站 VTable 展示做种列表（列同原需求），按每小时魔力降序

### 10.5 Dashboard.vue 实现步骤

1. 调用 `bonus_seeding_list` 汇总各站总魔力
2. 显示：总做种数、总时魔、未匹配数（可从 seed_association_data 获取）

### 10.6 API 调用规范

- 路径前缀：`plugin/PTBonusCalc/xxx`（如 `plugin/PTBonusCalc/bonus_seeding_list`）
- 鉴权：`auth: "bear"`
- 调用方式：`props.api.get(path)`、`props.api.post(path, body)`

---

## 11. 更优方案与改进

### 11.1 弹窗拆分（推荐）

原 Vuetify 版本把「站点地址映射」和「种子关联管理」放同一弹窗，内容多时难维护。

**改进**：拆成两个入口：
- 按钮「站点地址映射」→ 小弹窗，仅站点地址映射
- 按钮「种子关联管理」→ 大弹窗，仅未匹配种子与下拉选择

好处：职责清晰，加载更快，大列表时可独立滚动。

### 11.2 不保存即刷新（已有，强化）

`/seed_association_data` 支持传入当前表单配置，返回未匹配+选项。Config 中：

- 修改「显示站点」「同步下载器」「站点地址映射」后，点「刷新候选」按钮
- 不点保存即可看到最新未匹配列表和下拉选项，再决定是否保存

### 11.3 种子下拉优化

原版每行 VSelect 可能几百选项，易卡顿。

**改进**：
- 使用 VAutocomplete，支持输入筛选
- 或后端 options 做分页/懒加载（若需再扩展）

### 11.4 详情页交互

- 增加「刷新」按钮，重新请求 `bonus_seeding_list`
- 表格支持按列排序（魔力、大小、做种时长等）
- 移动端可考虑卡片式展示替代表格

### 11.5 Dashboard 简化

- 仅展示：总时魔、站点数、未匹配数、快捷跳转「去配置」「看详情」
- 不做复杂图表，保持轻量

### 11.6 数据结果与需求对齐

上述改动不改变：
- 配置项含义与保存格式
- 魔力计算公式与结果
- 匹配逻辑与 plugindata 结构

仅调整 UI 结构和交互，数据与业务逻辑保持一致。
