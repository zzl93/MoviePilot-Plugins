# -*- coding: utf-8 -*-
"""
PT 魔力计算器插件：展示主程序中是否有 PT 魔力计算所需的信息。
提供 test_main_data 接口、做种魔力列表 API、以及展示页（每种子魔力，按魔力排序）。
"""
import math
import re
from datetime import datetime
from typing import Any, List, Dict, Optional

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
            # 读取同步数据下载器配置（支持数组或逗号分隔字符串）
            sync_downloaders_value = config.get("sync_downloaders")
            if isinstance(sync_downloaders_value, list):
                self.sync_downloaders = [str(d).strip() for d in sync_downloaders_value if d]
            elif isinstance(sync_downloaders_value, str):
                self.sync_downloaders = [d.strip() for d in sync_downloaders_value.split(",") if d.strip()]
            else:
                self.sync_downloaders = []
            logger.info(f"PT魔力计算器插件初始化：同步数据下载器配置 = {self.sync_downloaders}")
            
            # 读取站点选择配置（支持数组或逗号分隔字符串）
            selected_sites_value = config.get("selected_sites")
            if isinstance(selected_sites_value, list):
                self.selected_sites = [str(s).strip() for s in selected_sites_value if s]
            elif isinstance(selected_sites_value, str):
                self.selected_sites = [s.strip() for s in selected_sites_value.split(",") if s.strip()]
            else:
                self.selected_sites = []
            logger.info(f"PT魔力计算器插件初始化：站点选择配置 = {self.selected_sites}")
            
            # 处理表单中的站点地址映射配置（site_address_mapping_* 字段）
            site_address_mappings_new = {}
            for key, value in config.items():
                if key.startswith("site_address_mapping_") and value:
                    site_domain = key.replace("site_address_mapping_", "")
                    # 解析逗号分隔的关键词
                    keywords = [k.strip() for k in str(value).split(",") if k.strip()]
                    if keywords:
                        site_address_mappings_new[site_domain] = keywords
                        logger.info(f"PT魔力计算器插件：保存站点地址映射 - {site_domain} -> {keywords}")
            if site_address_mappings_new != self.site_address_mappings:
                self.site_address_mappings = site_address_mappings_new
                logger.info(f"PT魔力计算器插件初始化：更新了站点地址映射配置")
            
            # 处理表单中的关联配置（torrent_mapping_* 字段）
            mappings = self._get_torrent_mappings()
            updated = False
            for key, value in config.items():
                if key.startswith("torrent_mapping_") and value:
                    # 解析字段名：torrent_mapping_{site_domain}_{torrent_key}
                    parts = key.replace("torrent_mapping_", "").split("_", 1)
                    if len(parts) >= 2:
                        site_domain = parts[0]
                        torrent_key = parts[1].replace("_", "|").replace("__", "/")
                        mapping_key = f"{site_domain}|{torrent_key}"
                        if mapping_key not in mappings or mappings[mapping_key] != value:
                            mappings[mapping_key] = value
                            updated = True
                            logger.info(f"PT魔力计算器插件：保存关联映射 - {mapping_key} -> {value}")
                elif key.startswith("torrent_mapping_") and not value:
                    # 删除关联
                    parts = key.replace("torrent_mapping_", "").split("_", 1)
                    if len(parts) >= 2:
                        site_domain = parts[0]
                        torrent_key = parts[1].replace("_", "|").replace("__", "/")
                        mapping_key = f"{site_domain}|{torrent_key}"
                        if mapping_key in mappings:
                            del mappings[mapping_key]
                            updated = True
                            logger.info(f"PT魔力计算器插件：删除关联映射 - {mapping_key}")
            
            if updated:
                self.save_data("torrent_mappings", mappings)
                logger.info(f"PT魔力计算器插件初始化：更新了 {len(mappings)} 个关联映射")
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
                logger.info("PT魔力计算器插件：get_form 中初始化 sites_helper")
            
            # 获取 Qbittorrent 下载器列表
            downloader_configs = ServiceConfigHelper.get_downloader_configs()
            qb_downloaders = [
                {"title": conf.name, "value": conf.name}
                for conf in downloader_configs
                if conf.type == "qbittorrent" and conf.enabled
            ]
            logger.info(f"PT魔力计算器插件：获取到 {len(qb_downloaders)} 个启用的 Qbittorrent 下载器")
            
            # 获取站点列表
            all_sites = self.sites_helper.get_indexers() or []
            enabled_sites = [
                {
                    "title": f"{site.get('name') or ''} ({StringUtils.get_url_domain(site.get('domain') or '')})",
                    "value": StringUtils.get_url_domain(site.get("domain") or "")
                }
                for site in all_sites
                if site.get("is_active") and site.get("domain")
            ]
            logger.info(f"PT魔力计算器插件：获取到 {len(enabled_sites)} 个启用的站点用于配置选择")
            
            # 获取未匹配的种子列表，按站点分组
            unmatched_torrents_data = self._get_bonus_seeding_data()
            unmatched_by_site = {}  # {site_domain: [torrents]}
            total_unmatched_count = 0
            for site_block in unmatched_torrents_data:
                domain = site_block.get("domain", "")
                site_name = site_block.get("site_name", "")
                unmatched_torrents = []
                for torrent in site_block.get("torrents", []):
                    # 确保只统计真正未匹配的（matched字段为False或不存在）
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
                            "torrent_key": torrent_id if torrent_id else f"{name}|{size}",
                        })
                        total_unmatched_count += 1
                if unmatched_torrents:
                    unmatched_by_site[domain] = {
                        "site_name": site_name,
                        "torrents": unmatched_torrents
                    }
            logger.info(f"PT魔力计算器插件：get_form 中统计到 {total_unmatched_count} 个未匹配种子，分布在 {len(unmatched_by_site)} 个站点")
            
            # 获取下载器种子字典，用于筛选
            downloader_torrents_dict = {}
            if self.sync_downloaders:
                try:
                    downloader_torrents_dict = self._fetch_downloader_torrents()
                except Exception as e:
                    logger.error(f"PT魔力计算器插件：获取下载器种子列表失败: {e}", exc_info=True)
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
                                "props": {"cols": 12},
                                "content": [
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
                                        "text": "配置站点域名与下载器中的地址关键词映射，用于更准确地匹配种子。例如：站点域名 lajidui.top 对应下载器中的地址关键词 ['lajidui', '垃圾堆']。",
                                        "props": {"class": "text-body-2 mb-4 text-grey"},
                                    },
                                ],
                            },
                        ],
                    },
        ]
        
        # 为每个站点添加地址映射配置
        site_mapping_rows = []
        for site in enabled_sites:
            site_domain = site.get("value", "")
            site_title = site.get("title", "")
            if not site_domain:
                continue
            
            # 获取该站点已配置的地址关键词
            current_keywords = self.site_address_mappings.get(site_domain, [])
            current_keywords_str = ",".join(current_keywords) if current_keywords else ""
            
            site_mapping_rows.append({
                "component": "VRow",
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
                                "component": "VTextField",
                                "props": {
                                    "model": f"site_address_mapping_{site_domain}",
                                    "label": "下载器地址关键词（逗号分隔）",
                                    "hint": "例如：lajidui,垃圾堆",
                                    "density": "compact",
                                },
                            },
                        ],
                    },
                ],
            })
        
        if site_mapping_rows:
            form_items[0]["content"].extend(site_mapping_rows)
        
        form_items[0]["content"].extend([
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
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
                                        "text": f"当前有 {total_unmatched_count} 个未匹配的站点种子（分布在 {len(unmatched_by_site)} 个站点）。点击下方站点展开，为每个种子选择对应的下载器种子进行关联。保存配置后关联生效。",
                                        "props": {"class": "text-body-2 mb-4 text-grey"},
                                    },
                                ],
                            },
                        ],
                    },
                ])
        
        # 按站点分组显示未匹配的种子，使用折叠面板（追加到VForm的content中）
        vform_content = form_items[0]["content"]
        
        mappings = self._get_torrent_mappings()
        default_data = {}
        
        # 创建站点折叠面板列表
        site_panels = []
        
        for site_domain, site_data in unmatched_by_site.items():
            site_name = site_data["site_name"]
            site_torrents = site_data["torrents"]
            
            # 第一步：先按站点筛选下载器种子（只保留属于当前站点的：tracker/名称含站点地址关键词）
            site_keywords = self.site_address_mappings.get(site_domain, [])
            if site_keywords:
                site_filtered_dict = {}
                for hash_value, dl_torrent in downloader_torrents_dict.items():
                    dl_tracker = (dl_torrent.get("tracker") or "").lower()
                    dl_name_lower = (dl_torrent.get("name") or "").lower()
                    for kw in site_keywords:
                        if kw and kw.lower().strip() in (dl_tracker + " " + dl_name_lower):
                            site_filtered_dict[hash_value] = dl_torrent
                            break
            else:
                site_filtered_dict = downloader_torrents_dict
            # 若按站点筛后为空，则退回用全部下载器种子
            if not site_filtered_dict and downloader_torrents_dict:
                site_filtered_dict = downloader_torrents_dict
            
            # 为每个站点的未匹配种子创建关联选择器列表（先站点 → 再名称 → 最后大小）
            site_torrent_rows = []
            for unmatched in site_torrents:
                site_torrent_size = unmatched['size']
                site_torrent_name = unmatched['name']
                size_min = site_torrent_size * 0.9
                size_max = site_torrent_size * 1.1
                filtered_downloader_torrents = []
                # 第二步：在站点池内按名称相似度筛选
                for hash_value, dl_torrent in site_filtered_dict.items():
                    dl_size = dl_torrent.get("total_size", 0)
                    dl_name = dl_torrent.get("name", "")
                    similarity = self._name_similarity(site_torrent_name, dl_name) if site_torrent_name and dl_name else 0.0
                    if similarity < 0.3:
                        continue
                    # 第三步：再按大小（±10%）筛选
                    if not (size_min <= dl_size <= size_max):
                        continue
                    filtered_downloader_torrents.append({
                        "title": f"{dl_name[:70]} ({StringUtils.str_filesize(dl_size)})",
                        "value": hash_value,
                    })
                filtered_downloader_torrents.sort(key=lambda x: x["title"])
                # 若名称+大小都筛不到，则放宽：仅名称>=0.3 或 仅大小±10%
                if not filtered_downloader_torrents:
                    for hash_value, dl_torrent in site_filtered_dict.items():
                        dl_size = dl_torrent.get("total_size", 0)
                        dl_name = dl_torrent.get("name", "")
                        similarity = self._name_similarity(site_torrent_name, dl_name) if site_torrent_name and dl_name else 0.0
                        if similarity >= 0.3 or (size_min <= dl_size <= size_max):
                            filtered_downloader_torrents.append({
                                "title": f"{dl_name[:70]} ({StringUtils.str_filesize(dl_size)})",
                                "value": hash_value,
                            })
                    filtered_downloader_torrents.sort(key=lambda x: x["title"])
                if not filtered_downloader_torrents and site_filtered_dict:
                    for hash_value, dl_torrent in list(site_filtered_dict.items())[:50]:
                        filtered_downloader_torrents.append({
                            "title": f"{dl_torrent.get('name', '')[:70]} ({StringUtils.str_filesize(dl_torrent.get('total_size', 0))})",
                            "value": hash_value,
                        })
                    filtered_downloader_torrents.sort(key=lambda x: x["title"])
                
                site_torrent_rows.append({
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "div",
                                    "text": f"{unmatched['name'][:60]}",
                                    "props": {"class": "text-body-2 mb-1"},
                                },
                                {
                                    "component": "div",
                                    "text": f"大小: {StringUtils.str_filesize(unmatched['size'])}",
                                    "props": {"class": "text-caption text-grey mb-2"},
                                },
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VSelect",
                                    "props": {
                                        "model": f"torrent_mapping_{site_domain}_{unmatched['torrent_key'].replace('|', '_').replace('/', '_')}",
                                        "label": f"选择下载器种子（已筛选 {len(filtered_downloader_torrents)} 个）",
                                        "items": filtered_downloader_torrents,
                                        "clearable": True,
                                        "hint": "已按站点→名称→大小筛选",
                                        "density": "compact",
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
            
            # 创建站点折叠面板
            site_panels.append({
                "component": "VExpansionPanel",
                "content": [
                    {
                        "component": "VExpansionPanelTitle",
                        "text": f"{site_name}（{len(site_torrents)} 个未匹配）"
                    },
                    {
                        "component": "VExpansionPanelText",
                        "content": site_torrent_rows
                    }
                ]
            })
        
        # 将折叠面板添加到表单中
        if site_panels:
            vform_content.append({
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
        
        return form_items, default_data

    def get_page(self) -> List[dict]:
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
                        "props": {"class": "text-sm"},
                        "content": [
                            {"component": "td", "props": {"class": "text-end"}, "text": str(idx)},
                            {"component": "td", "props": {"class": "text-start"}, "text": (r["name"] or "—")[:80] + ("..." if len(str(r["name"] or "")) > 80 else "")},
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
                panel_content = [
                    {"component": "div", "text": header, "props": {"class": "text-body-2 mb-2"}},
                    {
                        "component": "VTable",
                        "props": {"hover": True},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "content": [
                                        {"component": "th", "props": {"class": "text-end"}, "text": "序号"},
                                        {"component": "th", "props": {"class": "text-start ps-4"}, "text": "种子名"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "大小"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "做种人数"},
                                        {"component": "th", "props": {"class": "text-start"}, "text": "发布时间"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "T(周)"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "A值"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "A/GB"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "每小时魔力"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "关联状态"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "分享率"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "上传量"},
                                        {"component": "th", "props": {"class": "text-end"}, "text": "做种时长"},
                                    ],
                                }],
                            },
                            {"component": "tbody", "content": trs},
                        ],
                    },
                ]
            title_text = f"{site_name}（{len(torrents) if torrents else 0} 个做种，时魔 {total_bonus_fmt}）"
            panels.append({
                "component": "VExpansionPanel",
                "content": [
                    {"component": "VExpansionPanelTitle", "text": title_text},
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
        return [
            {
                "path": "/test_main_data",
                "endpoint": self._api_test_main_data,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "测试主程序数据",
                "description": "展示主程序中是否有插件需要的信息：用户当前做种信息、站点适配信息等",
            },
            {
                "path": "/raw_seeding_info",
                "endpoint": self._api_raw_seeding_info,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "原始做种信息",
                "description": "输出 DB/ORM 中 seeding_info 的原始值（未经整理的格式），用于对比 site/userdata 接口的返回",
            },
            {
                "path": "/bonus_seeding_list",
                "endpoint": self._api_bonus_seeding_list,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "做种魔力列表",
                "description": "各站点做种列表及每种子每小时魔力，可按魔力排序",
            },
            {
                "path": "/save_torrent_mapping",
                "endpoint": self._api_save_torrent_mapping,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "保存种子关联",
                "description": "保存站点种子与下载器种子的手动关联关系",
            },
            {
                "path": "/remove_torrent_mapping",
                "endpoint": self._api_remove_torrent_mapping,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "删除种子关联",
                "description": "删除站点种子与下载器种子的关联关系",
            },
            {
                "path": "/unmatched_torrents",
                "endpoint": self._api_unmatched_torrents,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取未匹配种子",
                "description": "获取未关联下载器的站点种子列表",
            },
            {
                "path": "/downloader_torrents",
                "endpoint": self._api_downloader_torrents,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取下载器种子列表",
                "description": "获取下载器中的种子列表，用于手动关联",
            },
            {
                "path": "/associate_torrent",
                "endpoint": self._api_associate_torrent,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "关联种子",
                "description": "手动关联站点种子与下载器种子",
            },
            {
                "path": "/remove_associate",
                "endpoint": self._api_remove_associate,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "取消关联",
                "description": "取消站点种子与下载器种子的关联",
            },
        ]

    def _get_bonus_seeding_data(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取各站点做种列表及每种子魔力（按每小时魔力降序），包含下载器关联数据。"""
        try:
            sites_to_query = []
            if site_id:
                try:
                    sid = int(site_id)
                    logger.info(f"PT魔力计算器插件：查询指定站点 site_id={site_id}")
                except (TypeError, ValueError):
                    sid = None
                    logger.error(f"PT魔力计算器插件：无效的 site_id={site_id}")
                site_info = self.site_oper.get(sid) if sid else None
                if site_info:
                    indexer = self.sites_helper.get_indexer(site_info.domain)
                    if indexer:
                        sites_to_query.append(indexer)
                        logger.info(f"PT魔力计算器插件：找到站点 {site_info.domain}")
            else:
                all_sites = [s for s in (self.sites_helper.get_indexers() or []) if s.get("is_active")]
                logger.info(f"PT魔力计算器插件：获取到 {len(all_sites)} 个启用站点")
                # 如果配置了站点选择，则只显示选中的站点
                if self.selected_sites:
                    selected_domains = set(self.selected_sites)
                    logger.info(f"PT魔力计算器插件：站点过滤配置生效，选中的站点域名 = {selected_domains}")
                    sites_to_query = [
                        s for s in all_sites
                        if StringUtils.get_url_domain(s.get("domain") or "") in selected_domains
                    ]
                    logger.info(f"PT魔力计算器插件：过滤后剩余 {len(sites_to_query)} 个站点")
                else:
                    sites_to_query = all_sites
                    logger.info(f"PT魔力计算器插件：未配置站点过滤，显示所有 {len(sites_to_query)} 个启用站点")
        except Exception as e:
            logger.error(f"PT魔力计算器插件：_get_bonus_seeding_data 获取站点列表失败: {e}", exc_info=True)
            raise

        # 拉取下载器种子数据（如果配置了同步数据下载器）
        downloader_torrents = {}
        if self.sync_downloaders:
            logger.info(f"PT魔力计算器插件：开始从 {len(self.sync_downloaders)} 个下载器拉取种子数据，下载器列表 = {self.sync_downloaders}")
            downloader_torrents = self._fetch_downloader_torrents()
            logger.info(f"PT魔力计算器插件：从下载器共获取到 {len(downloader_torrents)} 个种子")
            # 输出下载器种子名称样本（前10个），便于对比匹配
            if downloader_torrents:
                sample_count = min(10, len(downloader_torrents))
                logger.info(f"PT魔力计算器插件：下载器种子名称样本（前{sample_count}个）：")
                for idx, (hash_val, torrent) in enumerate(list(downloader_torrents.items())[:sample_count]):
                    logger.info(f"  {idx+1}. 名称={torrent.get('name', '')[:80]}, 大小={torrent.get('total_size', 0)}字节")
        else:
            logger.info("PT魔力计算器插件：未配置同步数据下载器，跳过下载器种子拉取")

        result = []
        for site in sites_to_query:
            try:
                domain_key = StringUtils.get_url_domain(site.get("domain") or "")
                userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
                if userdata_list and len(userdata_list) > 1:
                    userdata_list = sorted(
                        userdata_list,
                        key=lambda u: (u.updated_day or "", u.updated_time or ""),
                        reverse=True,
                    )
                userdata = userdata_list[0] if userdata_list else None
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
            matched_count = 0
            unmatched_count = 0
            unmatched_samples = []  # 记录未匹配的样本（用于日志输出）
            max_unmatched_samples = 3  # 最多记录3个未匹配样本
            
            logger.info(f"PT魔力计算器插件：站点 {domain_key} 开始处理 {len(seeding_list)} 个做种记录")
            
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
                # 匹配下载器种子
                matched_hash = None
                downloader_data = None
                if downloader_torrents:
                    # 对于前几个未匹配的种子，输出详细日志
                    debug_match = unmatched_count < max_unmatched_samples
                    matched_hash = self._match_torrent(s, domain_key, downloader_torrents, debug=debug_match)
                    if matched_hash and matched_hash in downloader_torrents:
                        downloader_data = downloader_torrents[matched_hash]
                        matched_count += 1
                    else:
                        unmatched_count += 1
                        # 记录未匹配样本
                        if len(unmatched_samples) < max_unmatched_samples:
                            unmatched_samples.append({
                                "name": s.get("name") or "",
                                "size": size_b,
                                "torrent_id": s.get("torrent_id")
                            })
                else:
                    unmatched_count += 1
                
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
            
            # 记录匹配统计
            if downloader_torrents:
                logger.info(f"PT魔力计算器插件：站点 {domain_key} 做种数 {len(rows)}，匹配下载器种子 {matched_count} 个，未匹配 {unmatched_count} 个")
                if unmatched_count > 0 and unmatched_samples:
                    logger.info(f"PT魔力计算器插件：未匹配样本（前{len(unmatched_samples)}个）：")
                    for sample in unmatched_samples:
                        logger.info(f"  - 名称={sample['name'][:60]}, 大小={sample['size']}, torrent_id={sample.get('torrent_id', 'N/A')}")
            
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
        sites_to_query = []
        if site_id:
            try:
                sid = int(site_id)
            except (TypeError, ValueError):
                sid = None
            site_info = self.site_oper.get(sid) if sid else None
            if site_info:
                indexer = self.sites_helper.get_indexer(site_info.domain)
                if indexer:
                    sites_to_query.append(indexer)
        else:
            sites_to_query = [s for s in (self.sites_helper.get_indexers() or []) if s.get("is_active")]

        result = {"sites": [], "message": ""}
        for site in sites_to_query:
            domain = site.get("domain") or ""

            if refresh:
                SiteChain().refresh_userdata(site=site)

            domain_key = StringUtils.get_url_domain(domain)
            userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
            if userdata_list and len(userdata_list) > 1:
                userdata_list = sorted(
                    userdata_list,
                    key=lambda u: (u.updated_day or "", u.updated_time or ""),
                    reverse=True,
                )
            userdata = userdata_list[0] if userdata_list else None

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
        sites_to_query = []
        if site_id:
            try:
                sid = int(site_id)
            except (TypeError, ValueError):
                sid = None
            site_info = self.site_oper.get(sid) if sid else None
            if site_info:
                indexer = self.sites_helper.get_indexer(site_info.domain)
                if indexer:
                    sites_to_query.append(indexer)
        else:
            sites_to_query = [s for s in (self.sites_helper.get_indexers() or []) if s.get("is_active")]

        result = {"sites": [], "message": ""}
        for site in sites_to_query:
            domain = site.get("domain") or ""
            if refresh:
                SiteChain().refresh_userdata(site=site)

            domain_key = StringUtils.get_url_domain(domain)
            userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
            if userdata_list and len(userdata_list) > 1:
                userdata_list = sorted(
                    userdata_list,
                    key=lambda u: (u.updated_day or "", u.updated_time or ""),
                    reverse=True,
                )
            userdata = userdata_list[0] if userdata_list else None

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
        """从配置的下载器中拉取全部种子数据"""
        all_torrents = {}
        for downloader_name in self.sync_downloaders:
            logger.info(f"PT魔力计算器插件：开始从下载器 {downloader_name} 拉取种子")
            qb_instance = self._get_qb_instance(downloader_name)
            if not qb_instance:
                logger.warning(f"PT魔力计算器插件：无法获取下载器 {downloader_name} 的实例，跳过")
                continue
            try:
                # 获取全部种子（不传 status，包含所有状态）
                # get_torrents 返回 (torrents_list, error)
                torrents, error = qb_instance.get_torrents()
                if error:
                    logger.error(f"PT魔力计算器插件：从下载器 {downloader_name} 获取种子时出错: {error}")
                    continue
                if not torrents:
                    logger.warning(f"PT魔力计算器插件：从下载器 {downloader_name} 获取到 0 个种子")
                    continue
                
                torrent_count = 0
                for torrent in torrents:
                    hash_value = torrent.get("hash")
                    if not hash_value:
                        continue
                    # 获取tracker信息（如果可用）
                    tracker = torrent.get("tracker") or ""
                    tags = torrent.get("tags") or []
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",") if t.strip()]
                    elif not isinstance(tags, list):
                        tags = []
                    
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
                        "tags": tags,
                    }
                    torrent_count += 1
                logger.info(f"PT魔力计算器插件：从下载器 {downloader_name} 成功获取到 {torrent_count} 个种子")
            except Exception as e:
                logger.error(f"PT魔力计算器插件：拉取下载器 {downloader_name} 种子失败: {e}", exc_info=True)
        return all_torrents

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
    
    def _match_torrent(self, site_torrent: Dict[str, Any], site_domain: str, downloader_torrents: Dict[str, Dict[str, Any]], debug: bool = False) -> Optional[str]:
        """匹配站点种子与下载器种子，返回下载器 hash"""
        torrent_id = site_torrent.get("torrent_id")
        name = site_torrent.get("name") or ""
        size = float(site_torrent.get("size") or 0)
        
        if debug:
            logger.info(f"PT魔力计算器插件：开始匹配站点种子 - 站点={site_domain}, 名称={name[:80]}, 大小={size}字节, torrent_id={torrent_id}")
        
        # 1. 优先使用手动映射
        mappings = self._get_torrent_mappings()
        if torrent_id:
            mapping_key = f"{site_domain}|{torrent_id}"
            if mapping_key in mappings:
                matched_hash = mappings[mapping_key]
                if debug:
                    logger.info(f"PT魔力计算器插件：通过手动映射(torrent_id)匹配成功，hash={matched_hash}")
                return matched_hash
        # 使用 name+size 作为键
        mapping_key = f"{site_domain}|{name}|{size}"
        if mapping_key in mappings:
            matched_hash = mappings[mapping_key]
            if debug:
                logger.info(f"PT魔力计算器插件：通过手动映射(name+size)匹配成功，hash={matched_hash}")
            return matched_hash
        
        # 2. 自动匹配：name + size（允许 ±1% 误差）
        if not name or size <= 0:
            if debug:
                logger.info(f"PT魔力计算器插件：匹配失败 - 站点种子名称或大小为空（name={name[:50] if name else 'None'}, size={size}）")
            return None
        
        size_min = size * 0.99
        size_max = size * 1.01
        site_name_normalized = self._normalize_name(name)
        
        # 获取站点地址映射关键词（用于优先匹配）
        site_address_keywords = self.site_address_mappings.get(site_domain, [])
        
        # 统计匹配尝试
        name_matched_count = 0
        size_matched_count = 0
        best_match_info = None  # 记录最接近的匹配（名称匹配但大小不匹配）
        best_similarity = 0.0
        
        # 先按大小筛选候选（提高性能）
        size_candidates = []
        address_matched_candidates = []  # 通过站点地址映射匹配的候选（优先）
        for hash_value, dl_torrent in downloader_torrents.items():
            dl_size = dl_torrent.get("total_size") or 0
            
            # 检查是否通过站点地址映射匹配（tracker或名称包含关键词）
            address_matched = False
            if site_address_keywords:
                dl_tracker = dl_torrent.get("tracker", "").lower()
                dl_name_lower = (dl_torrent.get("name") or "").lower()
                for keyword in site_address_keywords:
                    keyword_lower = keyword.lower().strip()
                    if keyword_lower and (keyword_lower in dl_tracker or keyword_lower in dl_name_lower):
                        address_matched = True
                        if debug:
                            logger.debug(f"PT魔力计算器插件：站点地址映射匹配 - 站点={site_domain}, 关键词={keyword}, "
                                       f"下载器种子={dl_torrent.get('name', '')[:50]}, tracker={dl_tracker[:50]}")
                        break
            
            if size_min <= dl_size <= size_max:
                if address_matched:
                    address_matched_candidates.append((hash_value, dl_torrent))
                else:
                    size_candidates.append((hash_value, dl_torrent))
        
        # 优先检查通过站点地址映射匹配的候选
        if address_matched_candidates:
            if debug:
                logger.info(f"PT魔力计算器插件：找到 {len(address_matched_candidates)} 个通过站点地址映射匹配的候选")
            for hash_value, dl_torrent in address_matched_candidates:
                dl_name = dl_torrent.get("name") or ""
                dl_size = dl_torrent.get("total_size") or 0
                
                # 名称相似度检查
                similarity = self._name_similarity(name, dl_name)
                name_matched = similarity >= 0.3  # 地址映射匹配时降低相似度阈值
                
                if name_matched:
                    name_matched_count += 1
                    if debug:
                        logger.info(f"PT魔力计算器插件：通过站点地址映射自动匹配成功 - 相似度={similarity:.2f}, "
                                   f"站点种子名称={name[:60]}, 下载器种子名称={dl_name[:60]}, "
                                   f"站点大小={size}字节, 下载器大小={dl_size}字节")
                    return hash_value
        
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
        
        # 遍历大小匹配的候选
        for hash_value, dl_torrent in size_candidates:
            dl_name = dl_torrent.get("name") or ""
            dl_size = dl_torrent.get("total_size") or 0
            
            # 名称相似度检查（改进版）
            similarity = self._name_similarity(name, dl_name)
            name_matched = similarity >= 0.5  # 相似度阈值50%
            
            if name_matched:
                name_matched_count += 1
                # 大小匹配（允许 ±1% 误差）
                if size_min <= dl_size <= size_max:
                    size_matched_count += 1
                    if debug:
                        logger.info(f"PT魔力计算器插件：自动匹配成功 - 相似度={similarity:.2f}, 站点种子名称={name[:60]}, "
                                   f"下载器种子名称={dl_name[:60]}, 站点大小={size}字节, 下载器大小={dl_size}字节")
                    return hash_value
                else:
                    # 记录最接近的匹配（名称匹配但大小不匹配）
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_match_info = {
                            "hash": hash_value,
                            "name": dl_name,
                            "size": dl_size,
                            "size_diff": abs(dl_size - size),
                            "size_diff_percent": abs(dl_size - size) / size * 100 if size > 0 else 0,
                            "similarity": similarity
                        }
        
        # 如果大小匹配的候选中没有找到，再检查所有下载器种子（名称匹配但大小不匹配的情况）
        if not best_match_info:
            for hash_value, dl_torrent in downloader_torrents.items():
                dl_name = dl_torrent.get("name") or ""
                dl_size = dl_torrent.get("total_size") or 0
                similarity = self._name_similarity(name, dl_name)
                
                if similarity >= 0.5:
                    name_matched_count += 1
                    if best_match_info is None or similarity > best_similarity:
                        best_similarity = similarity
                        best_match_info = {
                            "hash": hash_value,
                            "name": dl_name,
                            "size": dl_size,
                            "size_diff": abs(dl_size - size),
                            "size_diff_percent": abs(dl_size - size) / size * 100 if size > 0 else 0,
                            "similarity": similarity
                        }
        
        # 匹配失败，输出统计信息和对比样本（使用 INFO 级别，便于排查）
        if debug:
            logger.info(f"PT魔力计算器插件：匹配失败详情 - 站点={site_domain}, 站点种子名称={name[:100]}, 站点大小={size}字节")
            logger.info(f"PT魔力计算器插件：匹配统计 - 大小匹配候选数={len(size_candidates)}, 名称相似度>=0.5的种子数={name_matched_count}, 大小也匹配的种子数={size_matched_count}")
            if best_match_info:
                logger.info(f"PT魔力计算器插件：最接近的匹配 - 相似度={best_match_info.get('similarity', 0):.2f}, "
                           f"下载器种子名称={best_match_info['name'][:100]}, 下载器大小={best_match_info['size']}字节, "
                           f"大小差异={best_match_info['size_diff']:.0f}字节 ({best_match_info['size_diff_percent']:.2f}%)")
            elif name_matched_count == 0:
                logger.info(f"PT魔力计算器插件：未找到名称相似的下载器种子（相似度阈值>=0.5）")
                # 输出一些下载器种子名称作为对比参考（前5个）
                sample_torrents = list(downloader_torrents.items())[:5]
                if sample_torrents:
                    logger.info(f"PT魔力计算器插件：下载器种子名称对比样本（前5个，用于排查名称差异）：")
                    for idx, (hash_val, dl_torrent) in enumerate(sample_torrents):
                        dl_name = dl_torrent.get("name", "")[:100]
                        dl_size = dl_torrent.get("total_size", 0)
                        similarity = self._name_similarity(name, dl_name)
                        logger.info(f"  {idx+1}. 下载器种子名称={dl_name}, 大小={dl_size}字节, 相似度={similarity:.2f}")
        
        return None

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
                            "torrent_key": torrent_id if torrent_id else f"{name}|{size}",
                        })
            return {"success": True, "unmatched": unmatched, "count": len(unmatched)}
        except Exception as e:
            return {"success": False, "message": f"获取失败: {str(e)}"}

    def _api_downloader_torrents(self, site_domain: Optional[str] = Query(None), keyword: Optional[str] = Query(None)):
        """API：获取下载器种子列表，用于手动关联"""
        try:
            downloader_torrents = self._fetch_downloader_torrents()
            result = []
            for hash_value, torrent in downloader_torrents.items():
                name = torrent.get("name", "")
                # 如果提供了站点域名和关键词，进行筛选
                if site_domain and keyword:
                    # 简单匹配：名称包含关键词
                    if keyword.lower() not in name.lower():
                        continue
                result.append({
                    "hash": hash_value,
                    "name": name,
                    "size": torrent.get("total_size", 0),
                    "ratio": torrent.get("ratio", 0.0),
                    "downloader": torrent.get("downloader", ""),
                })
            # 按名称排序
            result.sort(key=lambda x: x.get("name", ""))
            return {"success": True, "torrents": result, "count": len(result)}
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
                # 保存关联
                self._save_torrent_mapping(site_domain, torrent_key, downloader_hash)
                logger.info(f"PT魔力计算器插件：手动关联成功 - 站点={site_domain}, 种子={torrent_key}, 下载器hash={downloader_hash}")
                return {"success": True, "message": "关联成功"}
            else:
                # 返回下载器种子列表供选择
                downloader_torrents = self._fetch_downloader_torrents()
                result = []
                for hash_value, torrent in downloader_torrents.items():
                    result.append({
                        "hash": hash_value,
                        "name": torrent.get("name", ""),
                        "size": torrent.get("total_size", 0),
                        "ratio": torrent.get("ratio", 0.0),
                        "downloader": torrent.get("downloader", ""),
                    })
                # 按名称排序
                result.sort(key=lambda x: x.get("name", ""))
                return {
                    "success": True,
                    "torrents": result,
                    "count": len(result),
                    "site_domain": site_domain,
                    "torrent_key": torrent_key
                }
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
