# -*- coding: utf-8 -*-
"""
PT 魔力计算器插件：展示主程序中是否有 PT 魔力计算所需的信息。
提供 test_main_data 接口、做种魔力列表 API、以及展示页（每种子魔力，按魔力排序）。
"""
import math
from datetime import datetime
from typing import Any, List, Dict, Optional

from fastapi import Query
from app.chain.site import SiteChain
from app.helper.module import ModuleHelper
from app.helper.sites import SitesHelper
from app.db.site_oper import SiteOper
from app.utils.string import StringUtils
from app.plugins import _PluginBase


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

    def init_plugin(self, config: dict = None):
        config = config or {}
        self.site_oper = SiteOper()
        self.sites_helper = SitesHelper()

    def get_state(self) -> bool:
        return True

    def stop_service(self):
        pass

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> tuple:
        return [], {}

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
        ]

    def _get_bonus_seeding_data(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取各站点做种列表及每种子魔力（按每小时魔力降序）。"""
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

        result = []
        for site in sites_to_query:
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
            for s in seeding_list:
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
                rows.append({
                    "name": s.get("name") or "—",
                    "size": size_b,
                    "seeders": N,
                    "pubdate": pubdate or "—",
                    "torrent_id": s.get("torrent_id"),
                    "T_weeks": round(T_weeks, 2),
                    "A_value": A_value,
                    "A_per_GB": A_per_GB,
                    "bonus_per_hour": bonus_per_hour,
                })
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
