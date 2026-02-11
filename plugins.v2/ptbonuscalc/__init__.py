# -*- coding: utf-8 -*-
"""
PT 魔力计算器插件（Vue模式）：展示主程序中是否有 PT 魔力计算所需的信息。
提供 form_options、test_main_data、做种魔力列表 API 等，配置页与详情页由 Vue 组件渲染。
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
    """解析发布时间字符串，返回距今周数；解析失败返回 None。"""
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
    """NexusPHP 魔力公式。返回 (B 每小时魔力, A 值, A/GB)。"""
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
    """根据 schema 获取 parser 的解析网页信息。"""
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
    """配置项解析：支持 list 或逗号分隔字符串。"""
    if isinstance(value, list):
        return [str(x).strip() for x in value if x]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _torrent_key(torrent_id: Any, name: str = "", size: Any = 0) -> str:
    """从 torrent_id / name / size 得到唯一键。"""
    return str(torrent_id) if torrent_id else f"{name or ''}|{size or 0}"


def _display_width(s: str) -> int:
    """计算字符串显示宽度，中文/日文=2，英文=1。"""
    if not s:
        return 0
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _truncate_by_display_width(s: str, max_width: int, suffix: str = "...") -> tuple:
    """按显示宽度截断字符串。返回 (截断后字符串, 是否被截断)。"""
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
    plugin_version = "0.2"
    plugin_author = "user"
    plugin_config_prefix = "ptbonuscalc_"
    plugin_order = 51
    auth_level = 1

    site_oper = None
    sites_helper = None
    sync_downloaders = []
    selected_sites = []
    site_address_mappings = {}

    def init_plugin(self, config: dict = None):
        config = config or {}
        try:
            self.site_oper = SiteOper()
            self.sites_helper = SitesHelper()
            self.sync_downloaders = _parse_list_config(config.get("sync_downloaders"))
            logger.info(f"PT魔力计算器插件初始化：同步数据下载器配置 = {self.sync_downloaders}")
            self.selected_sites = _parse_list_config(config.get("selected_sites"))
            logger.info(f"PT魔力计算器插件初始化：站点选择配置 = {self.selected_sites}")

            site_address_mappings_new = {}
            for key, value in config.items():
                if key.startswith("site_address_mapping_") and value:
                    site_domain = key.replace("site_address_mapping_", "")
                    keywords = _parse_list_config(value)
                    if keywords:
                        site_address_mappings_new[site_domain] = keywords
            if site_address_mappings_new != self.site_address_mappings:
                self.site_address_mappings = site_address_mappings_new

            mappings = self._get_torrent_mappings()
            auto_mappings = {}
            bonus_data = []
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
                        auto_mappings[f"{domain}|{torrent_key}"] = row["downloader_hash"]
            except Exception as e:
                logger.warning(f"PT魔力计算器插件：保存时获取自动匹配结果失败: {e}")

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
                elif mapping_key in mappings:
                    del mappings[mapping_key]

            for mk, hash_val in auto_mappings.items():
                if mk not in mappings:
                    mappings[mk] = hash_val

            self.save_data("torrent_mappings", mappings)

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

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        return "vue", "dist/assets"

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """Vue模式：配置页由 Config.vue 提供。"""
        return None, {}

    def get_page(self) -> Optional[List[dict]]:
        """Vue模式：详情页由 Page.vue 提供。"""
        return None

    def get_api(self) -> List[Dict[str, Any]]:
        api_specs = [
            ("/form_options", self._api_form_options, "GET", "表单选项", "Vue Config 用：sites、downloaders、address_keyword_options"),
            ("/test_main_data", self._api_test_main_data, "GET", "测试主程序数据", "展示主程序中是否有插件需要的信息"),
            ("/raw_seeding_info", self._api_raw_seeding_info, "GET", "原始做种信息", "输出 seeding_info 原始值"),
            ("/bonus_seeding_list", self._api_bonus_seeding_list, "GET", "做种魔力列表", "各站点做种列表及每种子每小时魔力"),
            ("/save_torrent_mapping", self._api_save_torrent_mapping, "POST", "保存种子关联", "保存站点种子与下载器种子的手动关联关系"),
            ("/remove_torrent_mapping", self._api_remove_torrent_mapping, "POST", "删除种子关联", "删除种子关联"),
            ("/unmatched_torrents", self._api_unmatched_torrents, "GET", "获取未匹配种子", "获取未关联下载器的站点种子列表"),
            ("/downloader_torrents", self._api_downloader_torrents, "GET", "获取下载器种子列表", "获取下载器中的种子列表"),
            ("/associate_torrent", self._api_associate_torrent, "POST", "关联种子", "手动关联站点种子与下载器种子"),
            ("/remove_associate", self._api_remove_associate, "POST", "取消关联", "取消关联"),
            ("/seed_association_data", self._api_seed_association_data, "POST", "获取种子关联数据", "未保存即刷新时用"),
        ]
        return [{"path": p, "endpoint": e, "methods": [m], "auth": "bear", "summary": s, "description": d} for p, e, m, s, d in api_specs]

    def _api_form_options(self) -> Dict[str, Any]:
        """Vue Config 表单选项：sites、downloaders、address_keyword_options、sites_with_config_data。"""
        try:
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
            sites_with_config_data = list(payload.get("sites_with_config_data", []))

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
                    logger.warning(f"PT魔力计算器插件：获取 tracker 域名失败: {e}")
            address_keyword_options = sorted(address_keyword_options)

            return {
                "sites": enabled_sites,
                "downloaders": qb_downloaders,
                "address_keyword_options": [{"title": d, "value": d} for d in address_keyword_options],
                "sites_with_config_data": sites_with_config_data,
            }
        except Exception as e:
            logger.error(f"PT魔力计算器插件：form_options 失败: {e}", exc_info=True)
            return {"sites": [], "downloaders": [], "address_keyword_options": [], "sites_with_config_data": []}

    def _get_sites_to_query(self, site_id: Optional[str] = None, filter_by_selected_sites: bool = True) -> List[Dict[str, Any]]:
        if site_id:
            try:
                sid = int(site_id)
            except (TypeError, ValueError):
                sid = None
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
        userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
        if userdata_list and len(userdata_list) > 1:
            userdata_list = sorted(userdata_list, key=lambda u: (u.updated_day or "", u.updated_time or ""), reverse=True)
        return userdata_list[0] if userdata_list else None

    def _keyword_to_domain(self, kw: Optional[str]) -> str:
        if not kw:
            return ""
        k = str(kw).strip()
        if "://" in k or ("." in k and "/" in k):
            return StringUtils.get_url_domain(k) or k
        return k

    def _get_bonus_seeding_data(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=True)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：_get_bonus_seeding_data 失败: {e}", exc_info=True)
            raise

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
                    continue
            except Exception as e:
                logger.error(f"PT魔力计算器插件：处理站点出错: {e}", exc_info=True)
                continue
            activity = getattr(userdata, "torrent_activity", None) or {}
            seeding_list = activity.get("seeding") if isinstance(activity, dict) else []
            if not isinstance(seeding_list, list):
                seeding_list = []
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
                size_val = float(s.get("size") or 0)
                if size_val > 0 and size_val < 1024 * 1024:
                    S_GB = size_val
                    size_b = int(size_val * (1024 ** 3))
                else:
                    S_GB = size_val / (1024 ** 3) if size_val else 0.0
                    size_b = int(size_val)
                N = int(s.get("seeders") or 0)
                pubdate = s.get("pubdate")
                T_weeks = _parse_pubdate_weeks(pubdate) or 0.0
                weight = s.get("weight") if "weight" in s else s.get("wi")
                weight = float(weight) if weight is not None else float(bonus_params.get("default_weight") or 1.0)
                if has_params:
                    bonus_per_hour, A_value, A_per_GB = _calc_bonus_per_hour(T_weeks, S_GB, N, T0, N0, B0, L, weight)
                else:
                    bonus_per_hour, A_value, A_per_GB = 0.0, 0.0, 0.0
                matched_hash = None
                downloader_data = None
                if downloader_torrents and self.site_address_mappings.get(domain_key):
                    candidate_torrents = self._get_candidate_torrents_for_site(domain_key, downloader_torrents, torrents_by_domain)
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
        return {"sites": self._get_bonus_seeding_data(site_id)}

    def _api_test_main_data(self, site_id: Optional[str] = Query(None), refresh: Optional[int] = Query(0)):
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
        sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=False)
        result = {"sites": [], "message": ""}
        for site in sites_to_query:
            domain = site.get("domain") or ""
            if refresh:
                SiteChain().refresh_userdata(site=site)
            domain_key = StringUtils.get_url_domain(domain)
            userdata = self._get_latest_userdata(domain_key)
            item = {"name": site.get("name"), "domain": domain_key, "userdata_exists": userdata is not None}
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
        try:
            module_manager = ModuleManager()
            qb_modules = list(module_manager.get_running_subtype_module(DownloaderType.Qbittorrent))
            if not qb_modules:
                return None
            return qb_modules[0].get_instance(downloader_name)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：获取下载器实例失败 {downloader_name}: {e}", exc_info=True)
            return None

    def _fetch_downloader_torrents(self) -> Dict[str, Dict[str, Any]]:
        all_torrents = {}
        for downloader_name in self.sync_downloaders:
            qb_instance = self._get_qb_instance(downloader_name)
            if not qb_instance:
                continue
            try:
                torrents, error = qb_instance.get_torrents()
                if error or not torrents:
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
                logger.error(f"PT魔力计算器插件：拉取下载器种子失败: {e}", exc_info=True)
        return all_torrents

    def _tracker_domain_group_key(self, tracker: str) -> str:
        if not (tracker or "").strip():
            return "__no_tracker__"
        return StringUtils.get_url_domain(tracker.strip()) or "__no_tracker__"

    def _build_torrents_by_tracker_domain(self, downloader_torrents: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
        by_domain: Dict[str, List[str]] = {}
        for hash_value, t in downloader_torrents.items():
            gk = self._tracker_domain_group_key((t.get("tracker") or "").strip())
            by_domain.setdefault(gk, []).append(hash_value)
        return by_domain

    def _get_candidate_torrents_for_site(
        self,
        site_domain: str,
        downloader_torrents: Dict[str, Dict[str, Any]],
        torrents_by_domain: Dict[str, List[str]],
    ) -> Dict[str, Dict[str, Any]]:
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
        downloader_torrents = self._fetch_downloader_torrents()
        result = []
        for hash_value, torrent in downloader_torrents.items():
            name = torrent.get("name", "")
            if keyword and keyword.lower() not in name.lower():
                continue
            result.append({"hash": hash_value, "name": name, "size": torrent.get("total_size", 0), "ratio": torrent.get("ratio", 0.0), "downloader": torrent.get("downloader", "")})
        result.sort(key=lambda x: x.get("name", ""))
        return result

    def _get_torrent_mappings(self) -> Dict[str, str]:
        try:
            mappings_data = self.get_data(key="torrent_mappings")
            return mappings_data if isinstance(mappings_data, dict) else {}
        except Exception:
            return {}

    def _save_torrent_mapping(self, site_domain: str, torrent_key: str, downloader_hash: str):
        mappings = self._get_torrent_mappings()
        mappings[f"{site_domain}|{torrent_key}"] = downloader_hash
        self.save_data("torrent_mappings", mappings)

    def _remove_torrent_mapping(self, site_domain: str, torrent_key: str):
        mappings = self._get_torrent_mappings()
        mapping_key = f"{site_domain}|{torrent_key}"
        if mapping_key in mappings:
            del mappings[mapping_key]
            self.save_data("torrent_mappings", mappings)

    def _build_seed_association_payload(self, override_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        backup = {"selected_sites": self.selected_sites, "sync_downloaders": self.sync_downloaders, "site_address_mappings": dict(self.site_address_mappings)}
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
                        unmatched_torrents.append({
                            "site_domain": domain,
                            "site_name": site_name,
                            "torrent_id": torrent.get("torrent_id"),
                            "name": torrent.get("name", ""),
                            "size": torrent.get("size", 0),
                            "torrent_key": _torrent_key(torrent.get("torrent_id"), torrent.get("name", ""), torrent.get("size", 0)),
                        })
                        total_unmatched_count += 1
                if unmatched_torrents:
                    unmatched_by_site[domain] = {
                        "site_name": site_name,
                        "torrents": unmatched_torrents,
                        "total_count": len(all_torrents),
                        "matched_count": len(all_torrents) - len(unmatched_torrents),
                    }

            site_fully_matched = self.get_data(key="site_fully_matched") or {}
            if isinstance(site_fully_matched, dict):
                for domain in list(unmatched_by_site.keys()):
                    if site_fully_matched.get(domain) is True:
                        total_unmatched_count -= len(unmatched_by_site[domain]["torrents"])
                        del unmatched_by_site[domain]

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
                    logger.error(f"PT魔力计算器插件：获取下载器种子失败: {e}", exc_info=True)

            for site_domain, site_data in unmatched_by_site.items():
                site_keywords = self.site_address_mappings.get(site_domain, [])
                if not site_keywords:
                    for unmatched in site_data["torrents"]:
                        unmatched["options"] = []
                    continue
                match_terms = set()
                for kw in site_keywords:
                    k = (kw or "").strip()
                    if k:
                        match_terms.add(k.lower())
                        d = self._keyword_to_domain(k)
                        if d:
                            match_terms.add(d.lower())
                site_filtered_dict = {}
                for hash_value, dl_torrent in downloader_torrents_dict.items():
                    combined = ((dl_torrent.get("tracker") or "") + " " + (dl_torrent.get("name") or "")).lower()
                    for term in match_terms:
                        if term and term in combined:
                            site_filtered_dict[hash_value] = dl_torrent
                            break
                if not site_filtered_dict and downloader_torrents_dict:
                    site_filtered_dict = downloader_torrents_dict
                total_dl = len(downloader_torrents_dict)
                if site_keywords and total_dl > 0 and len(site_filtered_dict) <= max(10, int(total_dl * 0.05)):
                    site_filtered_dict = downloader_torrents_dict

                site_filtered_list = []
                for hash_value, dl_torrent in site_filtered_dict.items():
                    name = dl_torrent.get("name") or ""
                    size_str = StringUtils.str_filesize(dl_torrent.get("total_size", 0))
                    suffix = f" ({size_str})"
                    max_name_width = 48 - _display_width(suffix)
                    short_name, _ = _truncate_by_display_width(name, max_name_width)
                    site_filtered_list.append({"display": short_name + suffix, "value": hash_value, "title": name})
                site_filtered_list.sort(key=lambda x: x["display"])
                for unmatched in site_data["torrents"]:
                    unmatched["options"] = site_filtered_list

            return {
                "total_unmatched_count": total_unmatched_count,
                "unmatched_by_site": unmatched_by_site,
                "sites_with_config_data": [block["domain"] for block in unmatched_torrents_data],
            }
        finally:
            self.selected_sites = backup["selected_sites"]
            self.sync_downloaders = backup["sync_downloaders"]
            self.site_address_mappings = backup["site_address_mappings"]

    def _normalize_name(self, name: str) -> str:
        if not name:
            return ""
        normalized = name.lower()
        for char in ['.', '_', '-', '[', ']', '(', ')', '{', '}']:
            normalized = normalized.replace(char, ' ')
        return re.sub(r'\s+', ' ', normalized).strip()

    def _name_similarity(self, name1: str, name2: str) -> float:
        if not name1 or not name2:
            return 0.0
        norm1, norm2 = self._normalize_name(name1), self._normalize_name(name2)
        if norm1 == norm2:
            return 1.0
        if norm1 in norm2 or norm2 in norm1:
            shorter, longer = min(len(norm1), len(norm2)), max(len(norm1), len(norm2))
            return shorter / longer if longer > 0 else 0.0
        words1, words2 = set(norm1.split()), set(norm2.split())
        if not words1 or not words2:
            return 0.0
        common, total = words1 & words2, words1 | words2
        return len(common) / len(total) if total else 0.0

    def _match_torrent(self, site_torrent: Dict[str, Any], site_domain: str, downloader_torrents: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        torrent_id = site_torrent.get("torrent_id")
        name = site_torrent.get("name") or ""
        size = float(site_torrent.get("size") or 0)
        mappings = self._get_torrent_mappings()
        if torrent_id and f"{site_domain}|{torrent_id}" in mappings:
            return (mappings[f"{site_domain}|{torrent_id}"], "手动映射")
        if f"{site_domain}|{name}|{size}" in mappings:
            return (mappings[f"{site_domain}|{name}|{size}"], "手动映射")
        if not name or size <= 0:
            return (None, None)
        size_min, size_max = size * 0.99, size * 1.01
        size_candidates = [(h, t) for h, t in downloader_torrents.items() if size_min <= (t.get("total_size") or 0) <= size_max]
        if len(size_candidates) > 100:
            candidates_with_sim = [(self._name_similarity(name, t.get("name") or ""), h, t) for h, t in size_candidates]
            candidates_with_sim.sort(reverse=True, key=lambda x: x[0])
            size_candidates = [(h, t) for _, h, t in candidates_with_sim[:50]]
        for hash_value, dl_torrent in size_candidates:
            if self._name_similarity(name, dl_torrent.get("name") or "") >= 0.5:
                return (hash_value, "大小+名称相似度")
        return (None, None)

    def _format_seeding_time(self, seconds: int) -> str:
        if seconds <= 0:
            return "0秒"
        days, hours, minutes = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60
        if days > 0:
            return f"{days}天{hours}小时"
        if hours > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{minutes}分钟"

    def _api_save_torrent_mapping(self, data: dict = Body(...)):
        try:
            site_domain, torrent_key, downloader_hash = data.get("site_domain"), data.get("torrent_key"), data.get("downloader_hash")
            if not site_domain or not torrent_key or not downloader_hash:
                return {"success": False, "message": "参数不完整"}
            self._save_torrent_mapping(site_domain, torrent_key, downloader_hash)
            return {"success": True, "message": "关联保存成功"}
        except Exception as e:
            return {"success": False, "message": f"保存失败: {str(e)}"}

    def _api_remove_torrent_mapping(self, data: dict = Body(...)):
        try:
            site_domain, torrent_key = data.get("site_domain"), data.get("torrent_key")
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            self._remove_torrent_mapping(site_domain, torrent_key)
            return {"success": True, "message": "关联删除成功"}
        except Exception as e:
            return {"success": False, "message": f"删除失败: {str(e)}"}

    def _api_unmatched_torrents(self, site_id: Optional[str] = Query(None)):
        try:
            data = self._get_bonus_seeding_data(site_id)
            unmatched = []
            for site_block in data:
                for torrent in site_block.get("torrents", []):
                    if not torrent.get("matched"):
                        unmatched.append({
                            "site_domain": site_block.get("domain"),
                            "site_name": site_block.get("site_name"),
                            "torrent_id": torrent.get("torrent_id"),
                            "name": torrent.get("name"),
                            "size": torrent.get("size"),
                            "torrent_key": _torrent_key(torrent.get("torrent_id"), torrent.get("name"), torrent.get("size")),
                        })
            return {"success": True, "unmatched": unmatched, "count": len(unmatched)}
        except Exception as e:
            return {"success": False, "message": f"获取失败: {str(e)}"}

    def _api_downloader_torrents(self, site_domain: Optional[str] = Query(None), keyword: Optional[str] = Query(None)):
        try:
            lst = self._downloader_torrents_list(keyword=keyword)
            return {"success": True, "torrents": lst, "count": len(lst)}
        except Exception as e:
            return {"success": False, "message": f"获取失败: {str(e)}"}

    def _api_associate_torrent(self, data: dict = Body(...)):
        try:
            site_domain, torrent_key, downloader_hash = data.get("site_domain"), data.get("torrent_key"), data.get("downloader_hash")
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            if downloader_hash:
                self._save_torrent_mapping(site_domain, torrent_key, downloader_hash)
                return {"success": True, "message": "关联成功"}
            lst = self._downloader_torrents_list()
            return {"success": True, "torrents": lst, "count": len(lst), "site_domain": site_domain, "torrent_key": torrent_key}
        except Exception as e:
            return {"success": False, "message": f"关联失败: {str(e)}"}

    def _api_remove_associate(self, data: dict = Body(...)):
        try:
            site_domain, torrent_key = data.get("site_domain"), data.get("torrent_key")
            if not site_domain or not torrent_key:
                return {"success": False, "message": "参数不完整"}
            self._remove_torrent_mapping(site_domain, torrent_key)
            return {"success": True, "message": "取消关联成功"}
        except Exception as e:
            return {"success": False, "message": f"取消关联失败: {str(e)}"}

    def _api_seed_association_data(self, data: Optional[dict] = Body(None)):
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
