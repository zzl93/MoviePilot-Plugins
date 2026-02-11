# -*- coding: utf-8 -*-
"""
PT 魔力计算器插件：展示主程序中是否有 PT 魔力计算所需的信息。
提供 test_main_data 接口、做种魔力列表 API、以及展示页（每种子魔力，按魔力排序）。
"""
import math
import re
import unicodedata
from datetime import datetime
from typing import Any, List, Dict, Optional, Tuple

from fastapi import Query, Body
from app.chain.site import SiteChain
from app.helper.module import ModuleHelper
from app.helper.sites import SitesHelper
from app.db.site_oper import SiteOper
from app.utils.string import StringUtils
from app.plugins import _PluginBase
from app.core.module import ModuleManager
from app.schemas.types import DownloaderType
from app.helper.service import ServiceConfigHelper
from app.log import logger


def _parse_pubdate_weeks(pubdate: Optional[str]) -> Optional[float]:
    """解析发布时间字符串，返回距今周数（与油猴脚本一致）；解析失败返回 None。"""
    if not pubdate or not isinstance(pubdate, str):
        return None
    s = pubdate.strip().replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ").replace("/", "-").replace("T", " ").rstrip("Z").strip()
    fmts = [
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ]
    for fmt, length in fmts:
        try:
            dt = datetime.strptime(s[:length], fmt)
            delta = datetime.now() - dt
            return max(0.0, delta.total_seconds() / (86400 * 7))
        except ValueError:
            continue
    return None


def _calc_bonus_per_hour(T_weeks: float, S_GB: float, N: int, T0: int, N0: int, B0: int, L: int, weight: float = 1.0) -> tuple:
    """
    NexusPHP 魔力公式：B = B0*(2/π)*arctan(A/L)，A = c1*S*c2*Wi。
    多站兼容：T0/N0/B0/L、做种数奖励、权重均来自 bonus_params；weight 可由做种项 s.get("weight") 或 default_weight。
    返回 (B 每小时魔力, A 值, A/GB)。
    """
    if T0 <= 0 or N0 <= 0 or B0 <= 0 or L <= 0:
        return 0.0, 0.0, 0.0
    c1 = 1.0 - math.pow(10, -T_weeks / T0)
    n = max(1, N)
    c2 = 1.0 + math.sqrt(2) * math.pow(10, -(n - 1) / (N0 - 1))
    A = c1 * S_GB * c2 * weight
    B = B0 * (2.0 / math.pi) * math.atan(A / L)
    ave = round(A / S_GB, 2) if S_GB > 0 else 0.0
    return round(B, 4), round(A, 2), ave


_PARSER_CLASSES_CACHE: Optional[List[Any]] = None


def _get_parser_parse_info(schema_value: str) -> Dict[str, Any]:
    """根据 schema 获取 parser 的解析网页信息（页面路径、提取项等）。"""
    global _PARSER_CLASSES_CACHE
    if not schema_value:
        return {}
    try:
        if _PARSER_CLASSES_CACHE is None:
            _PARSER_CLASSES_CACHE = ModuleHelper.load(
                "app.modules.indexer.parser",
                filter_func=lambda _, obj: hasattr(obj, "schema") and getattr(obj, "schema") is not None,
            )
        for parser_cls in _PARSER_CLASSES_CACHE or []:
            if not hasattr(parser_cls, "schema") or parser_cls.schema is None:
                continue
            if getattr(parser_cls.schema, "value", None) == schema_value:
                try:
                    parser = parser_cls(
                        site_name="",
                        url="https://example.com",
                        site_cookie="",
                        apikey="",
                        token="",
                    )
                    parse_pages = {
                        "user_detail": getattr(parser, "_user_detail_page", None),
                        "user_traffic": getattr(parser, "_user_traffic_page", None),
                        "torrent_seeding": getattr(parser, "_torrent_seeding_page", None),
                        "user_basic": getattr(parser, "_user_basic_page", None),
                        "mail_unread": getattr(parser, "_user_mail_unread_page", None),
                    }
                    parse_pages = {k: v for k, v in parse_pages.items() if v}
                    return {
                        "parse_pages": parse_pages,
                        "extracts": ["username", "userid", "upload", "download", "ratio", "bonus", "seeding", "leeching", "seeding_info", "message_unread"],
                    }
                except Exception:
                    return {"error": "parser init failed"}
        return {}
    except Exception as e:
        return {"error": str(e)}


def _parse_list_config(value: Any) -> List[str]:
    """配置项解析：支持 list 或逗号分隔字符串，返回非空字符串列表。"""
    if isinstance(value, list):
        return [str(x).strip() for x in value if x]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _torrent_key(torrent_id: Any, name: str = "", size: Any = 0) -> str:
    """从 torrent_id / name / size 得到唯一键，用于映射。"""
    return str(torrent_id) if torrent_id else f"{name or ''}|{size or 0}"


def _display_width(s: str) -> int:
    """计算字符串显示宽度，中文/日文=2，英文=1。"""
    if not s:
        return 0
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _truncate_by_display_width(s: str, max_width: int, suffix: str = "...") -> tuple:
    """
    按显示宽度截断字符串，兼容中文/日文（全角=2）与英文（半角=1）。
    返回 (截断后字符串, 是否被截断)。
    """
    if not s or max_width <= 0:
        return (s or "", False)
    width = 0
    for i, c in enumerate(s):
        w = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if width + w > max_width:
            return (s[:i] + suffix, True)
        width += w
    return (s, False)


class PTBonusCalc(_PluginBase):
    plugin_name = "PT魔力计算器"
    plugin_desc = "展示主程序中是否有 PT 魔力计算所需的信息：用户做种信息、站点适配信息、parser 解析网页信息。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/brushmanager.png"
    plugin_version = "0.1"
    plugin_author = "user"
    plugin_config_prefix = "ptbonuscalc_"
    plugin_order = 51
    auth_level = 1

    site_oper = None
    sites_helper = None
    sync_downloaders = []
    selected_sites = []
    site_address_mappings = {}  # {site_domain: [address_keywords]} 站点域名到下载器地址关键词的映射

    def init_plugin(self, config: dict = None):
        config = config or {}
        try:
            self.site_oper = SiteOper()
            self.sites_helper = SitesHelper()
            self.sync_downloaders = _parse_list_config(config.get("sync_downloaders"))
            logger.info(f"PT魔力计算器插件初始化：同步数据下载器配置 = {self.sync_downloaders}")
            self.selected_sites = _parse_list_config(config.get("selected_sites"))
            logger.info(f"PT魔力计算器插件初始化：站点选择配置 = {self.selected_sites}")
            
            # 处理表单中的站点地址映射配置（site_address_mapping_* 字段）
            site_address_mappings_new = {}
            for key, value in config.items():
                if key.startswith("site_address_mapping_") and value:
                    site_domain = key.replace("site_address_mapping_", "")
                    keywords = _parse_list_config(value)
                    if keywords:
                        site_address_mappings_new[site_domain] = keywords
                        logger.info(f"PT魔力计算器插件：保存站点地址映射 - {site_domain} -> {keywords}")
            if site_address_mappings_new != self.site_address_mappings:
                self.site_address_mappings = site_address_mappings_new
                logger.info(f"PT魔力计算器插件初始化：更新了站点地址映射配置")
            
            # 关联映射：先取当前自动匹配结果，再与表单中手动选择合并，一并写入 plugindata
            mappings = self._get_torrent_mappings()
            # 1) 用当前配置拉取做种数据，得到本次自动匹配结果（站点种子 -> 下载器 hash）
            auto_mappings = {}
            try:
                bonus_data = self._get_bonus_seeding_data()
                for site_block in bonus_data:
                    domain = site_block.get("domain", "")
                    for row in site_block.get("torrents", []):
                        if not row.get("downloader_hash"):
                            continue
                        torrent_key = _torrent_key(row.get("torrent_id"), row.get("name") or "", row.get("size", 0))
                        if not torrent_key:
                            continue
                        mapping_key = f"{domain}|{torrent_key}"
                        auto_mappings[mapping_key] = row["downloader_hash"]
            except Exception as e:
                logger.warning(f"PT魔力计算器插件：保存时获取自动匹配结果失败，仅保存手动选择: {e}")
            # 2) 表单中下拉选择覆盖或新增（手动优先）
            for key, value in config.items():
                if not key.startswith("torrent_mapping_"):
                    continue
                parts = key.replace("torrent_mapping_", "").split("_", 1)
                if len(parts) < 2:
                    continue
                site_domain = parts[0]
                torrent_key = parts[1].replace("_", "|").replace("__", "/")
                mapping_key = f"{site_domain}|{torrent_key}"
                if value:
                    mappings[mapping_key] = value
                    logger.info(f"PT魔力计算器插件：保存关联映射（表单） - {mapping_key} -> {value}")
                elif mapping_key in mappings:
                    del mappings[mapping_key]
                    logger.info(f"PT魔力计算器插件：删除关联映射 - {mapping_key}")
            # 3) 自动匹配结果填入未在表单中指定的项（合并）
            for mk, hash_val in auto_mappings.items():
                if mk not in mappings:
                    mappings[mk] = hash_val
            # 4) 写入 plugindata
            self.save_data("torrent_mappings", mappings)
            logger.info(f"PT魔力计算器插件初始化：关联映射已合并并写入 plugindata，共 {len(mappings)} 条")
            # 5) 计算各站点是否全匹配，写入 plugindata，下次打开设置时全匹配站点不参与种子关联管理
            site_fully_matched = {}
            try:
                for site_block in bonus_data:
                    domain = site_block.get("domain", "")
                    torrents = site_block.get("torrents", [])
                    if not torrents:
                        continue
                    all_matched = True
                    for row in torrents:
                        torrent_key = _torrent_key(row.get("torrent_id"), row.get("name") or "", row.get("size", 0))
                        if not torrent_key or f"{domain}|{torrent_key}" not in mappings:
                            all_matched = False
                            break
                    site_fully_matched[domain] = all_matched
                self.save_data("site_fully_matched", site_fully_matched)
                logger.info(f"PT魔力计算器插件初始化：站点全匹配标识已写入 plugindata，{site_fully_matched}")
            except Exception as e:
                logger.warning(f"PT魔力计算器插件：写入站点全匹配标识失败: {e}")
        except Exception as e:
            logger.error(f"PT魔力计算器插件初始化失败: {e}", exc_info=True)
            raise

    def get_state(self) -> bool:
        return True

    def stop_service(self):
        pass

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> tuple:
        """获取插件配置表单"""
        try:
            # 确保 sites_helper 已初始化
            if not self.sites_helper:
                self.sites_helper = SitesHelper()
            downloader_configs = ServiceConfigHelper.get_downloader_configs()
            qb_downloaders = [
                {"title": conf.name, "value": conf.name}
                for conf in downloader_configs
                if conf.type == "qbittorrent" and conf.enabled
            ]
            all_sites = self.sites_helper.get_indexers() or []
            enabled_sites = [
                {"title": f"{site.get('name') or ''} ({StringUtils.get_url_domain(site.get('domain') or '')})", "value": StringUtils.get_url_domain(site.get("domain") or "")}
                for site in all_sites
                if site.get("is_active") and site.get("domain")
            ]
            payload = self._build_seed_association_payload(None)
            total_unmatched_count = payload["total_unmatched_count"]
            unmatched_by_site = payload["unmatched_by_site"]
            sites_with_config_data = set(payload.get("sites_with_config_data", []))
            
            # 下载器地址关键词选项：先从已配置的站点地址映射收集（规范为域名），再在配置了同步下载器时合并 tracker 域名
            address_keyword_options = set()
            for keywords in self.site_address_mappings.values():
                for kw in keywords:
                    if not kw:
                        continue
                    d = self._keyword_to_domain(kw)
                    if d:
                        address_keyword_options.add(d)
            if self.sync_downloaders:
                try:
                    downloader_torrents = self._fetch_downloader_torrents()
                    for torrent in downloader_torrents.values():
                        tracker = torrent.get("tracker") or ""
                        if tracker:
                            domain = StringUtils.get_url_domain(tracker)
                            if domain:
                                address_keyword_options.add(domain)
                except Exception as e:
                    logger.warning(f"PT魔力计算器插件：获取下载器 tracker 域名失败: {e}")
            address_keyword_options = sorted(address_keyword_options)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：get_form 获取配置表单失败: {e}", exc_info=True)
            raise
        
        form_items = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "selected_sites",
                                            "label": "显示站点",
                                            "items": enabled_sites,
                                            "multiple": True,
                                            "chips": True,
                                            "hint": "选择要显示的站点，留空则显示所有启用站点",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "sync_downloaders",
                                            "label": "同步数据下载器",
                                            "items": qb_downloaders,
                                            "multiple": True,
                                            "chips": True,
                                            "hint": "从这些下载器拉取种子数据用于关联，留空则不关联下载器",
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "block": True,
                                            "color": "primary",
                                            "variant": "tonal",
                                            "onClick": "function(e) { site_mapping_dialog_open = true }",
                                        },
                                        "text": "打开站点地址映射与种子关联管理",
                                    },
                                ],
                            },
                        ],
                    },
                ]
            }
        ]
        
        # 下载器地址关键词多选下拉的选项（tracker 域名 + 已配置关键词）
        address_keyword_items = [{"title": d, "value": d} for d in address_keyword_options]
        
        # 站点地址映射只展示「显示站点」已选的站点（未选则不展示）；且仅展示有配置数据（有用户数据）的站点
        selected_domain_set = set(self.selected_sites) if self.selected_sites else None
        sites_for_mapping = [
            s for s in enabled_sites
            if s.get("value")
            and selected_domain_set is not None
            and s.get("value") in selected_domain_set
            and s.get("value") in sites_with_config_data
        ]
        
        # 为每个站点添加地址映射配置
        site_mapping_rows = []
        for site in sites_for_mapping:
            site_domain = site.get("value", "")
            site_title = site.get("title", "")
            if not site_domain:
                continue

            site_mapping_rows.append({
                "component": "VRow",
                "props": {"class": "mb-3"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "div",
                                "text": site_title,
                                "props": {"class": "text-body-2"},
                            },
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 8},
                        "content": [
                            {
                                "component": "VSelect",
                                "props": {
                                    "model": f"site_address_mapping_{site_domain}",
                                    "label": "下载器地址关键词",
                                    "items": address_keyword_items,
                                    "multiple": True,
                                    "chips": True,
                                    "hint": "从下载器种子 tracker 解析的域名中多选",
                                    "density": "compact",
                                },
                            },
                        ],
                    },
                ],
            })
        
        # 弹窗内容：站点地址映射 + 种子关联管理
        dialog_body = [
            {
                "component": "VDivider",
                "props": {"class": "my-2"},
            },
            {
                "component": "div",
                "text": "站点地址映射",
                "props": {"class": "text-h6 mb-2"},
            },
            {
                "component": "div",
                "text": "配置站点域名与下载器中的地址关键词映射，用于更准确地匹配种子。下拉选项来自同步数据下载器中种子的 tracker 域名，可多选。",
                "props": {"class": "text-body-2 mb-4 text-grey"},
            },
        ]
        dialog_body.extend(site_mapping_rows)
        dialog_body.extend([
            {
                "component": "VDivider",
                "props": {"class": "my-2"},
            },
            {
                "component": "div",
                "text": "种子关联管理",
                "props": {"class": "text-h6 mb-2"},
            },
            {
                "component": "div",
                "text": f"当前有 {total_unmatched_count} 个未匹配的站点种子（分布在 {len(unmatched_by_site)} 个站点）。点击下方站点展开，为每个种子选择对应的下载器种子进行关联。保存配置后关联生效。修改「显示站点」「同步数据下载器」或「站点地址映射」后需先保存再重新打开本设置页，候选列表才会更新。",
                "props": {"class": "text-body-2 mb-4 text-grey"},
            },
        ])
        
        # 按站点分组显示未匹配的种子，使用折叠面板
        mappings = self._get_torrent_mappings()
        default_data = {}
        # 站点地址映射多选下拉的默认选中值（仅对当前展示的站点）；已保存的 URL 规范为域名，避免与选项重复
        for site in sites_for_mapping:
            site_domain = site.get("value", "")
            if site_domain:
                raw = self.site_address_mappings.get(site_domain, [])
                seen = set()
                normalized = []
                for kw in raw:
                    d = self._keyword_to_domain(kw)
                    if d and d in address_keyword_options and d not in seen:
                        seen.add(d)
                        normalized.append(d)
                default_data[f"site_address_mapping_{site_domain}"] = normalized
        
        # 创建站点折叠面板列表
        site_panels = []
        
        for site_domain, site_data in unmatched_by_site.items():
            site_name = site_data["site_name"]
            site_torrents = site_data["torrents"]
            site_torrent_rows = []
            for unmatched in site_torrents:
                filtered_downloader_torrents = unmatched.get("options", [])
                site_torrent_rows.append({
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6, "style": "min-width: 0"},
                            "content": [
                                {
                                    "component": "div",
                                    "text": unmatched["name"] or "—",
                                    "props": {
                                        "class": "text-body-2 mb-1",
                                        "style": "overflow: hidden; text-overflow: ellipsis; white-space: nowrap",
                                        "title": unmatched["name"] or "",
                                    },
                                },
                                {
                                    "component": "div",
                                    "text": f"大小: {StringUtils.str_filesize(unmatched['size'])}",
                                    "props": {"class": "text-caption text-grey mb-1"},
                                },
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6, "style": "min-width: 0"},
                            "content": [
                                {
                                    "component": "VSelect",
                                    "props": {
                                        "model": f"torrent_mapping_{site_domain}_{unmatched['torrent_key'].replace('|', '_').replace('/', '_')}",
                                        "label": f"选择下载器种子（已筛选 {len(filtered_downloader_torrents)} 个）",
                                        "items": filtered_downloader_torrents,
                                        "itemTitle": "display",
                                        "itemValue": "value",
                                        "clearable": True,
                                        "hint": "已按站点地址映射筛选，可手动选择对应下载器种子",
                                        "density": "compact",
                                        "style": "min-width: 0; max-width: 100%",
                                    },
                                },
                            ],
                        },
                    ],
                })
                
                # 处理默认值
                mapping_key = f"{site_domain}|{unmatched['torrent_key']}"
                if mapping_key in mappings:
                    field_key = f"torrent_mapping_{site_domain}_{unmatched['torrent_key'].replace('|', '_').replace('/', '_')}"
                    default_data[field_key] = mappings[mapping_key]
            
            # 创建站点折叠面板（只展示未匹配的站点；标题显示种子总数、已匹配数、未匹配数）
            total_count = site_data.get("total_count", len(site_torrents))
            matched_count = site_data.get("matched_count", total_count - len(site_torrents))
            site_panels.append({
                "component": "VExpansionPanel",
                "content": [
                    {
                        "component": "VExpansionPanelTitle",
                        "props": {"style": "background-color: #e3f2fd; border-left: 4px solid #1976d2"},
                        "text": f"{site_name}（共 {total_count} 个做种，已匹配 {matched_count} 个，{len(site_torrents)} 个未匹配）"
                    },
                    {
                        "component": "VExpansionPanelText",
                        "content": [
                            {
                                "component": "div",
                                "props": {"style": "max-height: 280px; overflow-y: auto; overflow-x: hidden; padding-top: 12px"},
                                "content": site_torrent_rows
                            }
                        ]
                    }
                ]
            })
        
        # 将站点种子折叠面板添加到内容
        if site_panels:
            dialog_body.append({
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VExpansionPanels",
                                "props": {"multiple": True},
                                "content": site_panels
                            }
                        ]
                    }
                ]
            })

        # 将 VDialog 追加到表单，弹窗内容为站点地址映射与种子关联管理
        form_items[0]["content"].append({
            "component": "VDialog",
            "props": {
                "model": "site_mapping_dialog_open",
                "max-width": "65rem",
                "overlay-class": "v-overlay--scroll-blocked",
                "content-class": "v-card v-card--density-default v-card--variant-elevated rounded-t",
            },
            "content": [
                {
                    "component": "VCard",
                    "props": {"title": "站点地址映射与种子关联管理"},
                    "content": [
                        {
                            "component": "VDialogCloseBtn",
                            "props": {"model": "site_mapping_dialog_open"},
                        },
                        {
                            "component": "VCardText",
                            "props": {},
                            "content": dialog_body,
                        },
                    ],
                },
            ],
        })

        default_data["site_mapping_dialog_open"] = False

        return form_items, default_data

    def get_page(self) -> List[dict]:
        if not self.selected_sites:
            return [
                {
                    "component": "div",
                    "text": "请先在插件配置中勾选「显示站点」，保存后再查看本页。",
                    "props": {"class": "text-center pa-4"},
                }
            ]
        data = self._get_bonus_seeding_data()
        if not data:
            return [
                {
                    "component": "div",
                    "text": "暂无做种数据或未配置站点。请先刷新站点用户数据并确保有魔力参数（mybonus）。",
                    "props": {"class": "text-center pa-4"},
                }
            ]
        panels = []
        for site_block in data:
            site_name = site_block["site_name"]
            total_bonus = site_block["total_bonus_per_hour"]
            bonus_params = site_block.get("bonus_params") or {}
            decimals = int(bonus_params.get("hourly_bonus_decimals") or 2)
            total_bonus_fmt = f"{total_bonus:.{decimals}f}"
            torrents = site_block["torrents"]
            has_params = site_block["has_bonus_params"]
            if not torrents:
                panel_content = [{
                    "component": "div",
                    "text": f"{site_name}：无做种记录",
                    "props": {"class": "text-body-2"},
                }]
            else:
                header = f"共 {len(torrents)} 个做种，每小时总魔力：{total_bonus_fmt}"
                if not has_params:
                    header += " [未获取到站点魔力参数 T0/N0/B0/L，魔力为 0]"
                trs = []
                for idx, r in enumerate(torrents, 1):
                    trs.append({
                        "component": "tr",
                        "props": {"class": "text-sm", "style": "white-space: nowrap"},
                        "content": [
                            {"component": "td", "props": {"class": "text-end"}, "text": str(idx)},
                            {"component": "td", "props": {"class": "text-start", "style": "max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap", "title": r["name"] or ""}, "text": (r["name"] or "—")[:80] + ("..." if len(str(r["name"] or "")) > 80 else "")},
                            {"component": "td", "props": {"class": "text-end"}, "text": StringUtils.str_filesize(r["size"])},
                            {"component": "td", "props": {"class": "text-end"}, "text": str(r["seeders"])},
                            {"component": "td", "props": {"class": "text-start"}, "text": (r["pubdate"] or "—")},
                            {"component": "td", "props": {"class": "text-end"}, "text": str(r["T_weeks"])},
                            {"component": "td", "props": {"class": "text-end"}, "text": str(r["A_value"])},
                            {"component": "td", "props": {"class": "text-end"}, "text": str(r["A_per_GB"])},
                            {"component": "td", "props": {"class": "text-end font-weight-medium"}, "text": str(r["bonus_per_hour"])},
                            {"component": "td", "props": {"class": "text-end"}, "text": "✓ 已关联" if r.get("matched") else "✗ 未关联"},
                            {"component": "td", "props": {"class": "text-end"}, "text": f"{r.get('downloader_ratio', 0.0):.2f}" if r.get("matched") else "—"},
                            {"component": "td", "props": {"class": "text-end"}, "text": StringUtils.str_filesize(r.get("downloader_uploaded", 0)) if r.get("matched") else "—"},
                            {"component": "td", "props": {"class": "text-end"}, "text": self._format_seeding_time(r.get("downloader_seeding_time", 0)) if r.get("matched") else "—"},
                        ],
                    })
                inner_content = [
                    {"component": "div", "text": header, "props": {"class": "text-body-2 mb-2"}},
                    {
                        "component": "VTable",
                        "props": {"hover": True, "style": "table-layout: auto; min-width: max-content"},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "props": {"style": "white-space: nowrap"},
                                    "content": [
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "序号"},
                                        {"component": "th", "props": {"class": "text-start ps-4", "style": "white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis"}, "text": "种子名"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "大小"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "做种人数"},
                                        {"component": "th", "props": {"class": "text-start", "style": "white-space: nowrap"}, "text": "发布时间"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "T(周)"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "A值"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "A/GB"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "每小时魔力"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "关联状态"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "分享率"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "上传量"},
                                        {"component": "th", "props": {"class": "text-end", "style": "white-space: nowrap"}, "text": "做种时长"},
                                    ],
                                }],
                            },
                            {"component": "tbody", "content": trs},
                        ],
                    },
                ]
                panel_content = [
                    {
                        "component": "div",
                        "props": {"style": "max-height: 420px; overflow-y: auto; overflow-x: auto; padding-top: 12px"},
                        "content": inner_content
                    }
                ]
            title_text = f"{site_name}（{len(torrents) if torrents else 0} 个做种，时魔 {total_bonus_fmt}）"
            panels.append({
                "component": "VExpansionPanel",
                "content": [
                    {
                        "component": "VExpansionPanelTitle",
                        "props": {"style": "background-color: #e3f2fd; border-left: 4px solid #1976d2"},
                        "text": title_text
                    },
                    {"component": "VExpansionPanelText", "content": panel_content},
                ],
            })
        return [
            {
                "component": "VRow",
                "content": [{
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [{
                        "component": "VExpansionPanels",
                        "props": {"multiple": False},
                        "content": panels,
                    }],
                }],
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        api_specs = [
            ("/test_main_data", self._api_test_main_data, "GET", "测试主程序数据", "展示主程序中是否有插件需要的信息：用户当前做种信息、站点适配信息等"),
            ("/raw_seeding_info", self._api_raw_seeding_info, "GET", "原始做种信息", "输出 DB/ORM 中 seeding_info 的原始值（未经整理的格式），用于对比 site/userdata 接口的返回"),
            ("/bonus_seeding_list", self._api_bonus_seeding_list, "GET", "做种魔力列表", "各站点做种列表及每种子每小时魔力，可按魔力排序"),
            ("/save_torrent_mapping", self._api_save_torrent_mapping, "POST", "保存种子关联", "保存站点种子与下载器种子的手动关联关系"),
            ("/remove_torrent_mapping", self._api_remove_torrent_mapping, "POST", "删除种子关联", "删除站点种子与下载器种子的关联关系"),
            ("/unmatched_torrents", self._api_unmatched_torrents, "GET", "获取未匹配种子", "获取未关联下载器的站点种子列表"),
            ("/downloader_torrents", self._api_downloader_torrents, "GET", "获取下载器种子列表", "获取下载器中的种子列表，用于手动关联"),
            ("/associate_torrent", self._api_associate_torrent, "POST", "关联种子", "手动关联站点种子与下载器种子"),
            ("/remove_associate", self._api_remove_associate, "POST", "取消关联", "取消站点种子与下载器种子的关联"),
            ("/seed_association_data", self._api_seed_association_data, "POST", "获取种子关联数据（可传当前表单配置）", "用当前或传入的 selected_sites/sync_downloaders/site_address_mappings 计算未匹配列表与每行下拉候选，供前端「不保存即刷新」使用"),
        ]
        return [{"path": p, "endpoint": e, "methods": [m], "auth": "bear", "summary": s, "description": d} for p, e, m, s, d in api_specs]

    def _get_sites_to_query(self, site_id: Optional[str] = None, filter_by_selected_sites: bool = True) -> List[Dict[str, Any]]:
        """根据 site_id 或配置获取待查询站点列表。filter_by_selected_sites 为 True 时在未传 site_id 情况下按 selected_sites 过滤。"""
        if site_id:
            try:
                sid = int(site_id)
            except (TypeError, ValueError):
                sid = None
                logger.error(f"PT魔力计算器插件：无效的 site_id={site_id}")
            site_info = self.site_oper.get(sid) if sid else None
            if site_info:
                indexer = self.sites_helper.get_indexer(site_info.domain)
                if indexer:
                    return [indexer]
            return []
        all_sites = [s for s in (self.sites_helper.get_indexers() or []) if s.get("is_active")]
        if filter_by_selected_sites and self.selected_sites:
            selected_domains = set(self.selected_sites)
            return [s for s in all_sites if StringUtils.get_url_domain(s.get("domain") or "") in selected_domains]
        return all_sites

    def _get_latest_userdata(self, domain_key: str):
        """按域名取最新一条用户数据（按 updated_day/updated_time 降序取第一条）。"""
        userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
        if userdata_list and len(userdata_list) > 1:
            userdata_list = sorted(
                userdata_list,
                key=lambda u: (u.updated_day or "", u.updated_time or ""),
                reverse=True,
            )
        return userdata_list[0] if userdata_list else None

    def _keyword_to_domain(self, kw: Optional[str]) -> str:
        """将配置中的关键词/URL 规范为域名（用于下拉选项等）；非 URL 则返回原串 strip。"""
        if not kw:
            return ""
        k = str(kw).strip()
        if "://" in k or ("." in k and "/" in k):
            return StringUtils.get_url_domain(k) or k
        return k

    def _get_bonus_seeding_data(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取各站点做种列表及每种子魔力（按每小时魔力降序），包含下载器关联数据。"""
        try:
            sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=True)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：_get_bonus_seeding_data 获取站点列表失败: {e}", exc_info=True)
            raise

        # 拉取下载器种子数据（如果配置了同步数据下载器）
        if self.sync_downloaders:
            downloader_torrents = self._fetch_downloader_torrents()
            torrents_by_domain = self._build_torrents_by_tracker_domain(downloader_torrents)
        else:
            downloader_torrents = {}
            torrents_by_domain = {}

        result = []
        for site in sites_to_query:
            try:
                domain_key = StringUtils.get_url_domain(site.get("domain") or "")
                userdata = self._get_latest_userdata(domain_key)
                if not userdata:
                    logger.warning(f"PT魔力计算器插件：站点 {domain_key} 没有用户数据，跳过")
                    continue
            except Exception as e:
                logger.error(f"PT魔力计算器插件：处理站点 {site.get('domain', 'unknown')} 时出错: {e}", exc_info=True)
                continue
            activity = getattr(userdata, "torrent_activity", None) or {}
            seeding_list = activity.get("seeding") if isinstance(activity, dict) else []
            if not isinstance(seeding_list, list):
                seeding_list = []
            # 多站兼容：该站所有公式相关参数均来自 bonus_params（各站 mybonus 解析后入库），不按站点写死
            bonus_params = getattr(userdata, "bonus_params", None) or {}
            if not isinstance(bonus_params, dict):
                bonus_params = {}
            T0 = int(bonus_params.get("T0") or 0)
            N0 = int(bonus_params.get("N0") or 0)
            B0 = int(bonus_params.get("B0") or 0)
            L = int(bonus_params.get("L") or 0)
            has_params = all((T0, N0, B0, L))

            rows = []
            for idx, s in enumerate(seeding_list):
                if not isinstance(s, dict):
                    continue
                # 与 PT魔力计算器 一致：公式中 Si 单位 GB 且保留小数；做种数据 size 一般为字节
                size_val = float(s.get("size") or 0)
                if size_val > 0 and size_val < 1024 * 1024:
                    S_GB = size_val
                    size_b = int(size_val * (1024 ** 3))
                else:
                    S_GB = size_val / (1024 ** 3) if size_val else 0.0
                    size_b = int(size_val)
                N = int(s.get("seeders") or 0)
                pubdate = s.get("pubdate")
                T_weeks = _parse_pubdate_weeks(pubdate)
                if T_weeks is None:
                    T_weeks = 0.0
                # 零魔以做种数据中的标签为准：有 weight/wi 则用，无则用 default_weight；不做推断
                weight = s.get("weight") if "weight" in s else s.get("wi")
                weight = float(weight) if weight is not None else float(bonus_params.get("default_weight") or 1.0)
                if has_params:
                    bonus_per_hour, A_value, A_per_GB = _calc_bonus_per_hour(T_weeks, S_GB, N, T0, N0, B0, L, weight)
                else:
                    bonus_per_hour, A_value, A_per_GB = 0.0, 0.0, 0.0
                # 匹配下载器种子：仅当站点配置了站点地址映射时才做匹配
                matched_hash = None
                downloader_data = None
                if downloader_torrents and self.site_address_mappings.get(domain_key):
                    candidate_torrents = self._get_candidate_torrents_for_site(
                        domain_key, downloader_torrents, torrents_by_domain
                    )
                    matched_hash, _ = self._match_torrent(s, domain_key, candidate_torrents)
                    if matched_hash and matched_hash in downloader_torrents:
                        downloader_data = downloader_torrents[matched_hash]
                
                row_data = {
                    "name": s.get("name") or "—",
                    "size": size_b,
                    "seeders": N,
                    "pubdate": pubdate or "—",
                    "torrent_id": s.get("torrent_id"),
                    "T_weeks": round(T_weeks, 2),
                    "A_value": A_value,
                    "A_per_GB": A_per_GB,
                    "bonus_per_hour": bonus_per_hour,
                    "matched": matched_hash is not None,
                    "downloader_hash": matched_hash,
                }
                
                # 添加下载器数据
                if downloader_data:
                    row_data.update({
                        "downloader_ratio": downloader_data.get("ratio", 0.0),
                        "downloader_uploaded": downloader_data.get("uploaded", 0),
                        "downloader_downloaded": downloader_data.get("downloaded", 0),
                        "downloader_seeding_time": downloader_data.get("seeding_time", 0),
                        "downloader_state": downloader_data.get("state", ""),
                        "downloader_name": downloader_data.get("downloader", ""),
                    })
                
                rows.append(row_data)
            
            rows.sort(key=lambda x: x["bonus_per_hour"], reverse=True)
            # 与站点一致：每小时总魔力 = 公式 B（B0*(2/π)*atan(总A/L)）+ 做种数奖励（若 mybonus 页有解析到）
            # 站点 mybonus 页展示的 A 为一位小数（如 557.1），用该值算 B 才与网页时魔一致；用截断到一位小数避免我们总 A 略大导致 90.06
            total_A = sum(r["A_value"] for r in rows)
            total_A = math.floor(total_A * 10) / 10
            if has_params and total_A > 0:
                total_bonus = B0 * (2.0 / math.pi) * math.atan(total_A / L)
            else:
                total_bonus = 0.0
            seed_bonus = float(bonus_params.get("seeding_bonus_per_seed") or 0)
            seed_cap = int(bonus_params.get("seeding_bonus_cap") or 0)
            if seed_bonus > 0 and seed_cap > 0:
                total_bonus += seed_bonus * min(len(rows), seed_cap)
            # 与站点展示一致：按 bonus_params 中小数位截断（PTT 2 位、垃圾堆 3 位等，存库 hourly_bonus_decimals）
            decimals = int(bonus_params.get("hourly_bonus_decimals") or 2)
            total_bonus = math.floor(total_bonus * (10 ** decimals)) / (10 ** decimals)
            result.append({
                "site_name": site.get("name") or domain_key,
                "domain": domain_key,
                "bonus_params": bonus_params,
                "has_bonus_params": has_params,
                "torrents": rows,
                "total_bonus_per_hour": round(total_bonus, decimals),
            })
        return result

    def _api_bonus_seeding_list(self, site_id: Optional[str] = Query(None)):
        """API：做种魔力列表，可选 site_id。"""
        return {"sites": self._get_bonus_seeding_data(site_id)}

    def _api_test_main_data(self, site_id: Optional[str] = Query(None), refresh: Optional[int] = Query(0)):
        """
        测试主程序数据：展示主程序中是否有插件需要的信息。
        - 用户当前做种信息 (seeding_info)
        - 站点适配信息 (schema)
        - parser 解析网页信息
        参数: site_id 可选，不传则返回全部启用站点；refresh=1 则先刷新再返回
        """
        sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=False)
        result = {"sites": [], "message": ""}
        for site in sites_to_query:
            domain = site.get("domain") or ""
            if refresh:
                SiteChain().refresh_userdata(site=site)
            domain_key = StringUtils.get_url_domain(domain)
            userdata = self._get_latest_userdata(domain_key)
            site_result = dict(site)
            schema_val = site.get("parser") or site.get("schema") or ""
            site_result["parser"] = schema_val
            site_result["parser_parse_info"] = _get_parser_parse_info(schema_val)
            site_result["userdata"] = None
            seeding_info = []
            if userdata and userdata.seeding_info is not None:
                seeding_info = userdata.seeding_info if isinstance(userdata.seeding_info, list) else []
            site_result["seeding_info"] = seeding_info
            site_result["seeding_info_count"] = len(seeding_info)
            if userdata:
                site_result["userdata"] = {
                    "id": userdata.id,
                    "domain": userdata.domain,
                    "name": userdata.name,
                    "username": userdata.username,
                    "userid": userdata.userid,
                    "user_level": userdata.user_level,
                    "join_at": userdata.join_at,
                    "bonus": userdata.bonus,
                    "upload": userdata.upload,
                    "download": userdata.download,
                    "ratio": userdata.ratio,
                    "seeding": userdata.seeding,
                    "leeching": userdata.leeching,
                    "seeding_size": userdata.seeding_size,
                    "leeching_size": userdata.leeching_size,
                    "seeding_info": userdata.seeding_info or [],
                    "message_unread": userdata.message_unread,
                    "message_unread_contents": userdata.message_unread_contents or [],
                    "err_msg": userdata.err_msg,
                    "updated_day": userdata.updated_day,
                    "updated_time": userdata.updated_time,
                }

            result["sites"].append(site_result)

        result["count"] = len(result["sites"])
        return result

    def _api_raw_seeding_info(self, site_id: Optional[str] = Query(None), refresh: Optional[int] = Query(0)):
        """
        输出 seeding_info 的原始值（DB/ORM 直接读取，未经整理）。
        用于对比 site/userdata 接口的返回格式。
        参数: site_id 可选；refresh=1 则先刷新再返回
        """
        sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=False)
        result = {"sites": [], "message": ""}
        for site in sites_to_query:
            domain = site.get("domain") or ""
            if refresh:
                SiteChain().refresh_userdata(site=site)
            domain_key = StringUtils.get_url_domain(domain)
            userdata = self._get_latest_userdata(domain_key)
            item = {
                "name": site.get("name"),
                "domain": domain_key,
                "userdata_exists": userdata is not None,
            }
            if userdata:
                raw = getattr(userdata, "seeding_info", None)
                item["seeding_info_raw"] = raw
                item["seeding_info_type"] = type(raw).__name__
                item["seeding_info_repr"] = repr(raw) if raw is not None else "None"
                item["to_dict_seeding_info"] = userdata.to_dict().get("seeding_info") if hasattr(userdata, "to_dict") else None
            else:
                item["seeding_info_raw"] = None
                item["seeding_info_type"] = "NoneType"
                item["seeding_info_repr"] = "None"
                item["to_dict_seeding_info"] = None

            result["sites"].append(item)

        result["count"] = len(result["sites"])
        return result

    def _get_qb_instance(self, downloader_name: str):
        """通过 ModuleManager 获取 Qbittorrent 实例"""
        try:
            module_manager = ModuleManager()
            # 获取 Qbittorrent 模块（通过 subtype）
            qb_modules = list(module_manager.get_running_subtype_module(DownloaderType.Qbittorrent))
            if not qb_modules:
                return None
            # 使用第一个 Qbittorrent 模块
            qb_module = qb_modules[0]
            # 通过模块的 get_instance 方法获取下载器实例
            downloader_instance = qb_module.get_instance(downloader_name)
            return downloader_instance
        except Exception as e:
            logger.error(f"PT魔力计算器插件：获取下载器实例失败 {downloader_name}: {e}", exc_info=True)
            return None

    def _fetch_downloader_torrents(self) -> Dict[str, Dict[str, Any]]:
        """从配置的下载器中拉取全部种子数据，返回 hash -> 加工数据。"""
        all_torrents = {}
        for downloader_name in self.sync_downloaders:
            qb_instance = self._get_qb_instance(downloader_name)
            if not qb_instance:
                logger.warning(f"PT魔力计算器插件：无法获取下载器 {downloader_name} 的实例，跳过")
                continue
            try:
                torrents, error = qb_instance.get_torrents()
                if error:
                    logger.error(f"PT魔力计算器插件：从下载器 {downloader_name} 获取种子时出错: {error}")
                    continue
                if not torrents:
                    logger.warning(f"PT魔力计算器插件：从下载器 {downloader_name} 获取到 0 个种子")
                    continue
                for torrent in torrents:
                    hash_value = torrent.get("hash")
                    if not hash_value:
                        continue
                    tracker = torrent.get("tracker") or ""
                    if not tracker and isinstance(torrent.get("trackers"), list) and torrent["trackers"]:
                        first = torrent["trackers"][0]
                        tracker = (first.get("url") if isinstance(first, dict) else str(first)) or ""
                    all_torrents[hash_value] = {
                        "hash": hash_value,
                        "downloader": downloader_name,
                        "name": torrent.get("name") or "",
                        "total_size": torrent.get("total_size") or 0,
                        "ratio": torrent.get("ratio") or 0.0,
                        "uploaded": torrent.get("uploaded") or 0,
                        "downloaded": torrent.get("downloaded") or 0,
                        "seeding_time": torrent.get("seeding_time") or 0,
                        "state": torrent.get("state") or "",
                        "tracker": tracker,
                    }
            except Exception as e:
                logger.error(f"PT魔力计算器插件：拉取下载器 {downloader_name} 种子失败: {e}", exc_info=True)
        return all_torrents

    def _tracker_domain_group_key(self, tracker: str) -> str:
        """从 tracker URL 取二级或一级域名作为分组键（与 StringUtils.get_url_domain 一致：最后两级）。"""
        if not (tracker or "").strip():
            return "__no_tracker__"
        domain = StringUtils.get_url_domain(tracker.strip())
        return domain or "__no_tracker__"

    def _build_torrents_by_tracker_domain(
        self, downloader_torrents: Dict[str, Dict[str, Any]]
    ) -> Dict[str, List[str]]:
        """按 tracker 的二级/一级域名分组，返回 group_key -> [hash, ...]。只做分组，不输出原始数据。"""
        by_domain: Dict[str, List[str]] = {}
        for hash_value, t in downloader_torrents.items():
            tracker = (t.get("tracker") or "").strip()
            gk = self._tracker_domain_group_key(tracker)
            by_domain.setdefault(gk, []).append(hash_value)
        return by_domain

    def _get_candidate_torrents_for_site(
        self,
        site_domain: str,
        downloader_torrents: Dict[str, Dict[str, Any]],
        torrents_by_domain: Dict[str, List[str]],
    ) -> Dict[str, Dict[str, Any]]:
        """若配置了站点地址映射，从按 tracker 分组的 map 中取出该站对应域名的种子子集再匹配；未配置则返回全部。"""
        keywords = self.site_address_mappings.get(site_domain, [])
        if not keywords:
            return downloader_torrents
        keyword_domains = set()
        for kw in keywords:
            k = (kw or "").strip()
            if not k:
                continue
            d = StringUtils.get_url_domain(k) or k.lower()
            if d:
                keyword_domains.add(d)
        if not keyword_domains:
            return downloader_torrents
        candidate_hashes = []
        for gk, hashes in torrents_by_domain.items():
            if gk in keyword_domains:
                candidate_hashes.extend(hashes)
        if not candidate_hashes:
            return downloader_torrents
        return {h: downloader_torrents[h] for h in candidate_hashes if h in downloader_torrents}

    def _downloader_torrents_list(self, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """从配置的下载器拉取种子并转为列表，可选按名称关键词筛选，按名称排序。"""
        downloader_torrents = self._fetch_downloader_torrents()
        result = []
        for hash_value, torrent in downloader_torrents.items():
            name = torrent.get("name", "")
            if keyword and keyword.lower() not in name.lower():
                continue
            result.append({
                "hash": hash_value,
                "name": name,
                "size": torrent.get("total_size", 0),
                "ratio": torrent.get("ratio", 0.0),
                "downloader": torrent.get("downloader", ""),
            })
        result.sort(key=lambda x: x.get("name", ""))
        return result

    def _get_torrent_mappings(self) -> Dict[str, str]:
        """获取手动关联映射关系"""
        try:
            mappings_data = self.get_data(key="torrent_mappings")
            if not isinstance(mappings_data, dict):
                return {}
            return mappings_data
        except Exception:
            return {}

    def _save_torrent_mapping(self, site_domain: str, torrent_key: str, downloader_hash: str):
        """保存手动关联映射关系"""
        mappings = self._get_torrent_mappings()
        mapping_key = f"{site_domain}|{torrent_key}"
        mappings[mapping_key] = downloader_hash
        self.save_data("torrent_mappings", mappings)

    def _remove_torrent_mapping(self, site_domain: str, torrent_key: str):
        """删除关联映射关系"""
        mappings = self._get_torrent_mappings()
        mapping_key = f"{site_domain}|{torrent_key}"
        if mapping_key in mappings:
            del mappings[mapping_key]
            self.save_data("torrent_mappings", mappings)

    def _build_seed_association_payload(self, override_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        按当前或给定配置计算「未匹配种子 + 每行下拉候选」，供 get_form 与「用当前表单状态刷新」API 共用。
        override_config 可选：{ "selected_sites": [], "sync_downloaders": [], "site_address_mappings": {} }
        """
        backup = {
            "selected_sites": self.selected_sites,
            "sync_downloaders": self.sync_downloaders,
            "site_address_mappings": dict(self.site_address_mappings),
        }
        try:
            if override_config:
                if "selected_sites" in override_config:
                    self.selected_sites = _parse_list_config(override_config["selected_sites"])
                if "sync_downloaders" in override_config:
                    self.sync_downloaders = _parse_list_config(override_config["sync_downloaders"])
                if "site_address_mappings" in override_config and isinstance(override_config["site_address_mappings"], dict):
                    self.site_address_mappings = {k: (v if isinstance(v, list) else [str(v)]) for k, v in override_config["site_address_mappings"].items()}

            if not self.sites_helper:
                self.sites_helper = SitesHelper()
            unmatched_torrents_data = self._get_bonus_seeding_data()
            unmatched_by_site = {}
            total_unmatched_count = 0
            for site_block in unmatched_torrents_data:
                domain = site_block.get("domain", "")
                site_name = site_block.get("site_name", "")
                all_torrents = site_block.get("torrents", [])
                unmatched_torrents = []
                for torrent in all_torrents:
                    if not torrent.get("matched", False):
                        torrent_id = torrent.get("torrent_id")
                        name = torrent.get("name", "")
                        size = torrent.get("size", 0)
                        unmatched_torrents.append({
                            "site_domain": domain,
                            "site_name": site_name,
                            "torrent_id": torrent_id,
                            "name": name,
                            "size": size,
                            "torrent_key": _torrent_key(torrent_id, name, size),
                        })
                        total_unmatched_count += 1
                if unmatched_torrents:
                    total_count = len(all_torrents)
                    matched_count = total_count - len(unmatched_torrents)
                    unmatched_by_site[domain] = {
                        "site_name": site_name,
                        "torrents": unmatched_torrents,
                        "total_count": total_count,
                        "matched_count": matched_count,
                    }
            # 全匹配的站点不参与种子关联管理（不再展示下拉）
            site_fully_matched = self.get_data(key="site_fully_matched") or {}
            if isinstance(site_fully_matched, dict):
                for domain in list(unmatched_by_site.keys()):
                    if site_fully_matched.get(domain) is True:
                        total_unmatched_count -= len(unmatched_by_site[domain]["torrents"])
                        del unmatched_by_site[domain]

            # 种子关联管理只展示「显示站点」已选的站点（未选则不展示）
            if self.selected_sites:
                selected_domain_set = set(self.selected_sites)
                for domain in list(unmatched_by_site.keys()):
                    if domain not in selected_domain_set:
                        total_unmatched_count -= len(unmatched_by_site[domain]["torrents"])
                        del unmatched_by_site[domain]
            else:
                total_unmatched_count = 0
                unmatched_by_site.clear()

            downloader_torrents_dict = {}
            if self.sync_downloaders:
                try:
                    downloader_torrents_dict = self._fetch_downloader_torrents()
                except Exception as e:
                    logger.error(f"PT魔力计算器插件：_build_seed_association_payload 获取下载器种子失败: {e}", exc_info=True)

            # 为每个站点、每个未匹配种子计算下拉候选（含按域名放宽的站点池）；未配置站点地址映射则不提供候选
            for site_domain, site_data in unmatched_by_site.items():
                site_keywords = self.site_address_mappings.get(site_domain, [])
                if not site_keywords:
                    for unmatched in site_data["torrents"]:
                        unmatched["options"] = []
                    continue
                match_terms = set()
                for kw in site_keywords:
                    k = (kw or "").strip()
                    if not k:
                        continue
                    match_terms.add(k.lower())
                    d = self._keyword_to_domain(k)
                    if d:
                        match_terms.add(d.lower())
                site_filtered_dict = {}
                for hash_value, dl_torrent in downloader_torrents_dict.items():
                    dl_tracker = (dl_torrent.get("tracker") or "").lower()
                    dl_name_lower = (dl_torrent.get("name") or "").lower()
                    combined = dl_tracker + " " + dl_name_lower
                    for term in match_terms:
                        if term and term in combined:
                            site_filtered_dict[hash_value] = dl_torrent
                            break
                if not site_filtered_dict and downloader_torrents_dict:
                    site_filtered_dict = downloader_torrents_dict
                # 若配置了站点关键词但按地址只筛出很少（如≤10 或不足总数 5%），多半是下载器未返回 tracker，下拉改用全部种子
                total_dl = len(downloader_torrents_dict)
                if site_keywords and total_dl > 0 and len(site_filtered_dict) <= max(10, int(total_dl * 0.05)):
                    site_filtered_dict = downloader_torrents_dict

                # 按映射地址筛出的总条数；未匹配的站点种子其下拉中展示该站点下全部（地址已筛）下载器种子，由用户手动选
                # 显示：按显示宽度截断的名称 + 大小，总宽度包含大小；title 存全称供 HTML title 悬停展示
                _MAX_TOTAL_WIDTH = 48
                site_filtered_list = []
                for hash_value, dl_torrent in site_filtered_dict.items():
                    name = dl_torrent.get("name") or ""
                    size_str = StringUtils.str_filesize(dl_torrent.get("total_size", 0))
                    suffix = f" ({size_str})"
                    max_name_width = _MAX_TOTAL_WIDTH - _display_width(suffix)
                    short_name, _ = _truncate_by_display_width(name, max_name_width)
                    display = short_name + suffix
                    item = {"display": display, "value": hash_value, "title": name}
                    site_filtered_list.append(item)
                site_filtered_list.sort(key=lambda x: x["display"])
                for unmatched in site_data["torrents"]:
                    unmatched["options"] = site_filtered_list

            # 有配置数据的站点（有用户数据的站点），用于站点地址映射和种子关联管理仅展示这些站点
            sites_with_config_data = [block["domain"] for block in unmatched_torrents_data]
            return {
                "total_unmatched_count": total_unmatched_count,
                "unmatched_by_site": unmatched_by_site,
                "sites_with_config_data": sites_with_config_data,
            }
        finally:
            self.selected_sites = backup["selected_sites"]
            self.sync_downloaders = backup["sync_downloaders"]
            self.site_address_mappings = backup["site_address_mappings"]

    def _normalize_name(self, name: str) -> str:
        """标准化种子名称，去除特殊字符，便于匹配"""
        if not name:
            return ""
        # 转换为小写，去除常见特殊字符，保留字母数字和空格
        normalized = name.lower()
        # 替换常见分隔符为空格
        for char in ['.', '_', '-', '[', ']', '(', ')', '{', '}']:
            normalized = normalized.replace(char, ' ')
        # 合并多个空格为单个空格
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized
    
    def _name_similarity(self, name1: str, name2: str) -> float:
        """计算两个名称的相似度（0-1），简化版"""
        if not name1 or not name2:
            return 0.0
        norm1 = self._normalize_name(name1)
        norm2 = self._normalize_name(name2)
        
        # 完全匹配
        if norm1 == norm2:
            return 1.0
        
        # 包含匹配（一个包含另一个）
        if norm1 in norm2 or norm2 in norm1:
            # 计算包含部分的长度比例
            shorter = min(len(norm1), len(norm2))
            longer = max(len(norm1), len(norm2))
            return shorter / longer if longer > 0 else 0.0
        
        # 计算共同单词比例
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        if not words1 or not words2:
            return 0.0
        
        common_words = words1 & words2
        total_words = words1 | words2
        return len(common_words) / len(total_words) if total_words else 0.0
    
    def _match_torrent(self, site_torrent: Dict[str, Any], site_domain: str, downloader_torrents: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """匹配站点种子与下载器种子，返回 (下载器 hash, 成功原因) 或 (None, None)。"""
        torrent_id = site_torrent.get("torrent_id")
        name = site_torrent.get("name") or ""
        size = float(site_torrent.get("size") or 0)

        mappings = self._get_torrent_mappings()
        if torrent_id:
            mapping_key = f"{site_domain}|{torrent_id}"
            if mapping_key in mappings:
                return (mappings[mapping_key], "手动映射(torrent_id)")
        mapping_key = f"{site_domain}|{name}|{size}"
        if mapping_key in mappings:
            return (mappings[mapping_key], "手动映射(name+size)")

        if not name or size <= 0:
            return (None, None)
        
        size_min = size * 0.99
        size_max = size * 1.01
        size_candidates = [
            (hash_value, dl_torrent)
            for hash_value, dl_torrent in downloader_torrents.items()
            if size_min <= (dl_torrent.get("total_size") or 0) <= size_max
        ]
        # 如果大小匹配的候选很多，优先检查名称相似度高的
        if len(size_candidates) > 100:
            # 计算相似度并排序
            candidates_with_sim = []
            for hash_value, dl_torrent in size_candidates:
                dl_name = dl_torrent.get("name") or ""
                similarity = self._name_similarity(name, dl_name)
                candidates_with_sim.append((similarity, hash_value, dl_torrent))
            candidates_with_sim.sort(reverse=True, key=lambda x: x[0])
            size_candidates = [(h, t) for _, h, t in candidates_with_sim[:50]]  # 只检查前50个最相似的
        
        for hash_value, dl_torrent in size_candidates:
            dl_name = dl_torrent.get("name") or ""
            dl_size = dl_torrent.get("total_size") or 0
            similarity = self._name_similarity(name, dl_name)
            if similarity >= 0.5 and size_min <= dl_size <= size_max:
                return (hash_value, f"大小+名称相似度({similarity:.2f})")
        return (None, None)

    def _format_seeding_time(self, seconds: int) -> str:
        """格式化做种时长"""
        if seconds <= 0:
            return "0秒"
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        if days > 0:
            return f"{days}天{hours}小时"
        elif hours > 0:
            return f"{hours}小时{minutes}分钟"
        else:
            return f"{minutes}分钟"

    def _api_save_torrent_mapping(self, data: dict = Body(...)):
        """API：保存种子关联"""
        try:
            site_domain = data.get("site_domain")
            torrent_key = data.get("torrent_key")  # torrent_id 或 name|size
            downloader_hash = data.get("downloader_hash")
            if not site_domain or not torrent_key or not downloader_hash:
                return {"success": False, "message": "参数不完整"}
            self._save_torrent_mapping(site_domain, torrent_key, downloader_hash)
            return {"success": True, "message": "关联保存成功"}
        except Exception as e:
            return {"success": False, "message": f"保存失败: {str(e)}"}

    def _api_remove_torrent_mapping(self, data: dict = Body(...)):
        """API：删除种子关联"""
        try:
            site_domain = data.get("site_domain")
            torrent_key = data.get("torrent_key")
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            self._remove_torrent_mapping(site_domain, torrent_key)
            return {"success": True, "message": "关联删除成功"}
        except Exception as e:
            return {"success": False, "message": f"删除失败: {str(e)}"}

    def _api_unmatched_torrents(self, site_id: Optional[str] = Query(None)):
        """API：获取未匹配的站点种子列表"""
        try:
            data = self._get_bonus_seeding_data(site_id)
            unmatched = []
            for site_block in data:
                domain = site_block.get("domain")
                for torrent in site_block.get("torrents", []):
                    if not torrent.get("matched"):
                        torrent_id = torrent.get("torrent_id")
                        name = torrent.get("name")
                        size = torrent.get("size")
                        unmatched.append({
                            "site_domain": domain,
                            "site_name": site_block.get("site_name"),
                            "torrent_id": torrent_id,
                            "name": name,
                            "size": size,
                            "torrent_key": _torrent_key(torrent_id, name, size),
                        })
            return {"success": True, "unmatched": unmatched, "count": len(unmatched)}
        except Exception as e:
            return {"success": False, "message": f"获取失败: {str(e)}"}

    def _api_downloader_torrents(self, site_domain: Optional[str] = Query(None), keyword: Optional[str] = Query(None)):
        """API：获取下载器种子列表，用于手动关联"""
        try:
            lst = self._downloader_torrents_list(keyword=keyword)
            return {"success": True, "torrents": lst, "count": len(lst)}
        except Exception as e:
            return {"success": False, "message": f"获取失败: {str(e)}"}
    
    def _api_associate_torrent(self, data: dict = Body(...)):
        """API：关联种子（带对话框选择下载器种子）"""
        try:
            site_domain = data.get("site_domain")
            torrent_key = data.get("torrent_key")
            downloader_hash = data.get("downloader_hash")
            
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            
            if downloader_hash:
                self._save_torrent_mapping(site_domain, torrent_key, downloader_hash)
                logger.info(f"PT魔力计算器插件：手动关联成功 - 站点={site_domain}, 种子={torrent_key}, 下载器hash={downloader_hash}")
                return {"success": True, "message": "关联成功"}
            lst = self._downloader_torrents_list()
            return {"success": True, "torrents": lst, "count": len(lst), "site_domain": site_domain, "torrent_key": torrent_key}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：关联种子失败: {e}", exc_info=True)
            return {"success": False, "message": f"关联失败: {str(e)}"}
    
    def _api_remove_associate(self, data: dict = Body(...)):
        """API：取消关联"""
        try:
            site_domain = data.get("site_domain")
            torrent_key = data.get("torrent_key")
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            self._remove_torrent_mapping(site_domain, torrent_key)
            logger.info(f"PT魔力计算器插件：取消关联成功 - 站点={site_domain}, 种子={torrent_key}")
            return {"success": True, "message": "取消关联成功"}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：取消关联失败: {e}", exc_info=True)
            return {"success": False, "message": f"取消关联失败: {str(e)}"}

    def _api_seed_association_data(self, data: Optional[dict] = Body(None)):
        """
        用当前或传入的表单配置计算未匹配种子与每行下拉候选。
        body 可选：{ "selected_sites": [], "sync_downloaders": [], "site_address_mappings": {} }
        供前端在未保存时刷新种子关联区域。
        """
        try:
            override = None
            if data and isinstance(data, dict):
                override = {}
                if "selected_sites" in data:
                    override["selected_sites"] = data["selected_sites"]
                if "sync_downloaders" in data:
                    override["sync_downloaders"] = data["sync_downloaders"]
                if "site_address_mappings" in data and isinstance(data["site_address_mappings"], dict):
                    override["site_address_mappings"] = data["site_address_mappings"]
                if not override:
                    override = None
            return self._build_seed_association_payload(override)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：获取种子关联数据失败: {e}", exc_info=True)
            return {"total_unmatched_count": 0, "unmatched_by_site": {}}
