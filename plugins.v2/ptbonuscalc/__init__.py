# -*- coding: utf-8 -*-
"""
PT 魔力计算器插件（Vue模式）：展示主程序中是否有 PT 魔力计算所需的信息。
提供 form_options、test_main_data、做种魔力列表 API 等，配置页与详情页由 Vue 组件渲染。
"""
import math
import random
import re
from typing import Any, List, Dict, Optional, Tuple

from fastapi import Query, Body
from app.chain.site import SiteChain
from app.helper.sites import SitesHelper
from app.db import SessionFactory
from app.db.site_oper import SiteOper
from app.utils.string import StringUtils
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.helper.service import ServiceConfigHelper
from app.log import logger

from app.plugins.ptbonuscalc.server import seedinfo_oper, utils, downloader_fetcher
from app.plugins.ptbonuscalc.server.sync_bonus_data import sync_from_fetch


def _compute_suggested_downloader_matches(
    pairs: List[Tuple[Any, Any, Any, Any]],
    dl_candidates: List[Tuple[Any, Any]],
) -> Dict[int, Tuple[Any, Any]]:
    """
    仅用于展示的「建议匹配」：名称分词后至少 2 个词匹配、大小完全一致、一对一，不写库。
    """
    used_dl_ids = {dl.id for (_, _, dl, _) in pairs if dl is not None}
    available = [(dl, snap) for (dl, snap) in dl_candidates if dl.id not in used_dl_ids]
    unmatched = [(site_seed, site_snap) for (site_seed, site_snap, dl, _) in pairs if dl is None]
    suggested = {}
    for site_seed, _ in unmatched:
        name_s = (site_seed.name or "").strip()
        size_s = int(site_seed.size or 0)
        for i, (dl, snap) in enumerate(available):
            name_d = (dl.downloader_torrent_name or "").strip()
            size_d = int(dl.size or 0)
            if utils.name_match_at_least_n_tokens(name_s, name_d, 2) and size_s == size_d:
                suggested[site_seed.id] = (dl, snap)
                available.pop(i)
                break
    return suggested


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
    primary_downloaders: List[str] = []
    aux_downloaders: List[str] = []
    selected_sites = []
    site_address_mappings = {}

    def init_plugin(self, config: dict = None):
        config = config or {}
        try:
            seedinfo_oper.init_seedinfo_db()
            self.site_oper = SiteOper()
            self.sites_helper = SitesHelper()
            self.eventmanager.add_event_listener(EventType.SiteRefreshed, self._on_site_refreshed)
            primary = utils.parse_list_config(config.get("primary_downloaders"))
            aux = utils.parse_list_config(config.get("aux_downloaders"))
            self.primary_downloaders = primary
            self.aux_downloaders = aux
            if primary or aux:
                self.sync_downloaders = list(dict.fromkeys(primary + aux))
            else:
                self.sync_downloaders = utils.parse_list_config(config.get("sync_downloaders"))
            logger.info(f"PT魔力计算器插件初始化：主下载器={primary} 辅下载器={aux} 同步下载器={self.sync_downloaders}")
            self.selected_sites = utils.parse_list_config(config.get("selected_sites"))
            logger.info(f"PT魔力计算器插件初始化：站点选择配置 = {self.selected_sites}")

            site_address_mappings_new = {}
            for key, value in config.items():
                if key.startswith("site_address_mapping_") and value:
                    site_domain = key.replace("site_address_mapping_", "")
                    keywords = utils.parse_list_config(value)
                    if keywords:
                        site_address_mappings_new[site_domain] = keywords
                        logger.info(f"PT魔力计算器[init_plugin] 保存配置 key={key} value={value} -> site_domain={site_domain} keywords={keywords}")
            if site_address_mappings_new != self.site_address_mappings:
                self.site_address_mappings = site_address_mappings_new

            for domain in self.selected_sites or []:
                if not domain:
                    continue
                indexer = self.sites_helper.get_indexer(domain) if self.sites_helper else None
                if indexer and indexer.get("schema") == "NexusPhp":
                    existing_info = self.get_data(f"site_info_{domain}") or {}
                    if not isinstance(existing_info, dict):
                        existing_info = {}
                    site_info = {
                        **existing_info,
                        "name": indexer.get("name") or domain,
                        "domain": StringUtils.get_url_domain(indexer.get("domain") or "") or domain,
                        "url": indexer.get("url") or indexer.get("domain") or "",
                        "cookie": indexer.get("cookie") or "",
                        "schema": indexer.get("schema") or "NexusPhp",
                        "ua": indexer.get("ua") or "",
                        "proxy": indexer.get("proxy"),
                    }
                    if site_info.get("url"):
                        stored = self.get_data(f"site_info_{domain}") or {}
                        if isinstance(stored, dict):
                            if "total_seed_count" in stored:
                                site_info["total_seed_count"] = stored["total_seed_count"]
                            if "total_bonus_per_hour" in stored:
                                site_info["total_bonus_per_hour"] = stored["total_bonus_per_hour"]
                        self.save_data(f"site_info_{domain}", site_info)

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
                        torrent_key = utils.torrent_key(row.get("torrent_id"), row.get("name") or "", row.get("size", 0))
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
                    if mapping_key not in mappings:
                        self._save_torrent_mapping(site_domain, torrent_key, value)
                    mappings[mapping_key] = value
                else:
                    if mapping_key in mappings:
                        seedinfo_oper.remove_seed_info(None, site_domain, torrent_key)
                    mappings.pop(mapping_key, None)

            for mk, hash_val in auto_mappings.items():
                if mk not in mappings:
                    domain, tkey = mk.split("|", 1)
                    self._save_torrent_mapping(domain, tkey, hash_val)

            mappings = self._get_torrent_mappings()

            site_fully_matched = {}
            try:
                for site_block in bonus_data:
                    domain = site_block.get("domain", "")
                    torrents = site_block.get("torrents", [])
                    if not torrents:
                        continue
                    all_matched = True
                    for row in torrents:
                        torrent_key = utils.torrent_key(row.get("torrent_id"), row.get("name") or "", row.get("size", 0))
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
            ("/downloader_tracker_options", self._api_downloader_tracker_options, "POST", "下载器 Tracker 地址选项", "根据传入的下载器列表返回站点地址映射下拉所需的 Tracker 地址列表"),
            ("/site_list", self._api_site_list, "POST", "站点列表", "仅返回已选站点及存储的种子总数、总时魔，用于做种页表头"),
            ("/bonus_data", self._api_bonus_data, "POST", "站点做种明细", "按 site_id 返回该站点的做种行与下载器候选，用于展开某站点时拉取"),
            ("/save_data", self._api_save_data, "POST", "保存数据", "根据前端传入的关联数据批量保存/删除"),
        ]
        return [{"path": p, "endpoint": e, "methods": [m], "auth": "bear", "summary": s, "description": d} for p, e, m, s, d in api_specs]

    def _api_form_options(self) -> Dict[str, Any]:
        """Vue Config 表单选项：sites、downloaders 从主项目站点表/下载器配置实时获取。"""
        try:
            logger.info(f"PT魔力计算器[form_options] 开始")
            if not self.sites_helper:
                self.sites_helper = SitesHelper()
            downloader_configs = ServiceConfigHelper.get_downloader_configs()
            enabled_downloaders = [
                {"title": conf.name, "value": conf.name}
                for conf in downloader_configs
                if conf.enabled
            ]
            db = SessionFactory()
            try:
                site_list = SiteOper(db).list_order_by_pri()
                enabled_sites = [
                    {"title": f"{s.name or ''} ({StringUtils.get_url_domain(s.domain or '')})", "value": StringUtils.get_url_domain(s.domain or "")}
                    for s in site_list
                    if s.is_active and s.domain
                ]
            finally:
                db.close()
            payload = self._build_seed_association_payload(None)
            sites_with_config_data = list(payload.get("sites_with_config_data", []))

            address_keyword_options = set()
            logger.info(f"PT魔力计算器[form_options] site_address_mappings={self.site_address_mappings}")
            for keywords in self.site_address_mappings.values():
                for kw in keywords:
                    if not kw:
                        continue
                    d = utils.keyword_to_domain(kw)
                    if d:
                        address_keyword_options.add(d)
            address_keyword_options = sorted(address_keyword_options)
            logger.info(f"PT魔力计算器[form_options] address_keyword_options={list(address_keyword_options)[:20]}")

            suggested_site_mappings = {}
            addr_list = list(address_keyword_options)
            for site in enabled_sites:
                site_domain = (site.get("value") or "").strip().lower()
                if not site_domain:
                    continue
                matches = []
                for addr in addr_list:
                    addr_lower = addr.lower()
                    if site_domain == addr_lower:
                        matches.append(addr)
                    elif addr_lower.endswith("." + site_domain):
                        matches.append(addr)
                    elif site_domain.endswith("." + addr_lower):
                        matches.append(addr)
                if matches:
                    suggested_site_mappings[site_domain] = matches

            return {
                "sites": enabled_sites,
                "downloaders": enabled_downloaders,
                "address_keyword_options": [{"title": d, "value": d} for d in address_keyword_options],
                "sites_with_config_data": sites_with_config_data,
                "suggested_site_mappings": suggested_site_mappings,
            }
        except Exception as e:
            logger.error(f"PT魔力计算器插件：form_options 失败: {e}", exc_info=True)
            return {"sites": [], "downloaders": [], "address_keyword_options": [], "sites_with_config_data": [], "suggested_site_mappings": {}}

    def _api_downloader_tracker_options(self, data: Optional[dict] = Body(None)) -> Dict[str, Any]:
        """根据前端传入的下载器列表，从下载器拉取种子并提取 Tracker 地址，返回站点地址映射下拉选项。"""
        try:
            data = data or {}
            primary = utils.parse_list_config(data.get("primary_downloaders"))
            aux = utils.parse_list_config(data.get("aux_downloaders"))
            if primary or aux:
                downloader_names = list(dict.fromkeys(primary + aux))
            else:
                downloader_names = utils.parse_list_config(data.get("sync_downloaders"))
            if not downloader_names:
                return {"address_keyword_options": []}
            address_keyword_options = set()
            downloader_torrents = downloader_fetcher.fetch_downloader_torrents(downloader_names) or []
            for torrent in downloader_torrents:
                tracker = torrent.get("tracker") or ""
                if not tracker:
                    continue
                domain = utils.tracker_full_host(tracker)
                if domain:
                    address_keyword_options.add(domain)
            sorted_options = sorted(address_keyword_options)
            return {"address_keyword_options": [{"title": d, "value": d} for d in sorted_options]}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：downloader_tracker_options 失败: {e}", exc_info=True)
            return {"address_keyword_options": []}

    def _on_site_refreshed(self, event: Any) -> None:
        """站点刷新后，拉取页面解析并同步 torrent_activity、bonus_params 到插件存储"""
        try:
            data = getattr(event, "event_data", None) or {}
            site_id = data.get("site_id")
            indexers = self.sites_helper.get_indexers() or []
            if site_id == "*":
                for site in indexers:
                    if site.get("is_active") and site.get("schema") == "NexusPhp":
                        sync_from_fetch(site, self)
            elif site_id and str(site_id).isdigit():
                site_obj = self.site_oper.get(int(site_id))
                if site_obj:
                    site_dict = self.sites_helper.get_indexer(site_obj.domain)
                    if site_dict and site_dict.get("schema") == "NexusPhp":
                        sync_from_fetch(site_dict, self)
        except Exception as e:
            logger.debug(f"PT魔力计算器插件：SiteRefreshed 同步失败: {e}")

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
        if filter_by_selected_sites:
            if not self.selected_sites:
                return []
            selected_domains = set(self.selected_sites)
            return [s for s in all_sites if StringUtils.get_url_domain(s.get("domain") or "") in selected_domains]
        return all_sites

    def _get_sites_to_query_from_plugindata(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """仅从 plugindata 取站点列表，供 Page 接口使用，不查主项目表。site_id 仅支持域名字符串筛选。"""
        if site_id and isinstance(site_id, str) and "." in site_id:
            info = self.get_data(f"site_info_{site_id}")
            if info and isinstance(info, dict) and info.get("url"):
                return [info]
            if not self.selected_sites:
                return []
        if not self.selected_sites:
            return []
        domains_to_query = list(self.selected_sites)
        if site_id and isinstance(site_id, str) and "." in site_id and site_id in self.selected_sites:
            domains_to_query = [site_id]
        result = []
        for domain in domains_to_query:
            if not domain:
                continue
            info = self.get_data(f"site_info_{domain}")
            if info and isinstance(info, dict) and info.get("url"):
                result.append(info)
        return result

    def _get_latest_userdata(self, domain_key: str):
        userdata_list = self.site_oper.get_userdata_by_domain(domain_key)
        if userdata_list and len(userdata_list) > 1:
            userdata_list = sorted(userdata_list, key=lambda u: (u.updated_day or "", u.updated_time or ""), reverse=True)
        return userdata_list[0] if userdata_list else None

    def _get_bonus_seeding_data(self, site_id: Optional[str] = None, use_plugindata_only: bool = False) -> List[Dict[str, Any]]:
        try:
            if use_plugindata_only:
                sites_to_query = self._get_sites_to_query_from_plugindata(site_id)
            else:
                sites_to_query = self._get_sites_to_query(site_id, filter_by_selected_sites=True)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：_get_bonus_seeding_data 失败: {e}", exc_info=True)
            raise

        result = []
        for site in sites_to_query:
            try:
                raw_domain = (site.get("domain") or "").strip()
                domain_key = StringUtils.get_url_domain(raw_domain) or raw_domain
                site_url = (site.get("url") or "").strip().rstrip("/")
                if not site.get("schema") and site_url:
                    site = {**site, "schema": "NexusPhp"}
                bonus_params = self.get_data(f"bonus_params_{domain_key}") or {}
                if not isinstance(bonus_params, dict):
                    bonus_params = {}

                pairs = []
                try:
                    pairs = seedinfo_oper.list_seed_with_latest_snapshot_by_site(None, domain_key, self.sync_downloaders)
                    if not pairs and raw_domain and raw_domain != domain_key:
                        pairs = seedinfo_oper.list_seed_with_latest_snapshot_by_site(None, raw_domain, self.sync_downloaders)
                        if pairs:
                            domain_key = raw_domain
                except Exception:
                    pass
                if not pairs and site.get("schema") == "NexusPhp" and site.get("url"):
                    try:
                        sync_from_fetch(site, self)
                        pairs = seedinfo_oper.list_seed_with_latest_snapshot_by_site(None, domain_key, self.sync_downloaders)
                    except Exception as e:
                        logger.warning(f"PT魔力计算器插件：无数据时页面解析失败 {domain_key}: {e}")

                T0 = int(bonus_params.get("T0") or 0)
                N0 = int(bonus_params.get("N0") or 0)
                B0 = int(bonus_params.get("B0") or 0)
                L = int(bonus_params.get("L") or 0)
                has_params = all((T0, N0, B0, L))

                address_mappings = list(self.site_address_mappings.get(domain_key, []))
                mapping_domains = set()
                for kw in address_mappings:
                    if kw:
                        d = utils.keyword_to_domain(str(kw).strip())
                        if d:
                            mapping_domains.add(d)

                dl_candidates = []
                if self.sync_downloaders and mapping_domains:
                    try:
                        dl_candidates = seedinfo_oper.list_downloader_seeds_with_latest_snapshot(
                            None, self.sync_downloaders, tracker_domains=list(mapping_domains)
                        )
                    except Exception:
                        pass
                suggested = _compute_suggested_downloader_matches(pairs, dl_candidates)

                rows = []
                for site_seed, site_snap, dl_seed, dl_snap in pairs:
                    if site_snap and site_snap.web_status == "not_exists":
                        continue
                    display_dl_seed, display_dl_snap = dl_seed, dl_snap
                    if display_dl_seed is None and site_seed.id in suggested:
                        display_dl_seed, display_dl_snap = suggested[site_seed.id]
                    used_suggested = (
                        display_dl_seed is not None
                        and site_seed.id in suggested
                        and suggested[site_seed.id][0] == display_dl_seed
                    )
                    seeders = (site_snap.seeders or 0) if site_snap else 0
                    weight = (site_snap.weight or 1.0) if site_snap else 1.0
                    size_b = int(site_seed.size or 0)
                    S_GB = size_b / (1024 ** 3) if size_b else 0.0
                    pubdate = site_seed.pubdate
                    T_weeks = utils.parse_pubdate_weeks(pubdate) or 0.0
                    if site_snap and site_snap.T_weeks:
                        T_weeks = float(site_snap.T_weeks) or T_weeks
                    weight_val = float(weight) if weight is not None else float(bonus_params.get("default_weight") or 1.0)
                    if has_params:
                        bonus_per_hour, A_value, A_per_GB = utils.calc_bonus_per_hour(T_weeks, S_GB, seeders, T0, N0, B0, L, weight_val)
                    else:
                        bonus_per_hour = 0.0
                        A_value = 0.0
                        A_per_GB = 0.0
                    torrent_id = site_seed.torrent_id if site_seed.torrent_id and str(site_seed.torrent_id).isdigit() else None
                    tkey = site_seed.torrent_id or utils.torrent_key(torrent_id, site_seed.name or "", size_b)

                    raw_hash = (display_dl_seed.downloader_hash if display_dl_seed else None) or None
                    seed_tracker_domain = (display_dl_seed.tracker_domain if display_dl_seed else "") or (utils.tracker_domain_group_key(display_dl_seed.tracker or "") if display_dl_seed and display_dl_seed.tracker else "")
                    if mapping_domains:
                        matched_hash = raw_hash if (raw_hash and seed_tracker_domain in mapping_domains) else None
                    else:
                        matched_hash = None
                    row_data = {
                        "name": site_seed.name or "—",
                        "size": size_b,
                        "seeders": seeders,
                        "pubdate": pubdate or "—",
                        "torrent_id": torrent_id,
                        "torrent_key": tkey,
                        "T_weeks": round(T_weeks, 2),
                        "A_value": A_value,
                        "A_per_GB": A_per_GB,
                        "bonus_per_hour": bonus_per_hour,
                        "matched": bool(matched_hash),
                        "downloader_hash": raw_hash,
                        "detail_url": (getattr(site_seed, "detail_url", None) or "") or (f"{site_url}/details.php?id={torrent_id}" if (site_url and torrent_id) else ""),
                    }
                    if raw_hash and display_dl_seed:
                        row_data["downloader_name"] = display_dl_seed.downloader_name or ""
                        row_data["downloader_torrent_name"] = (getattr(display_dl_seed, "downloader_torrent_name", None) or "") or ""
                        row_data["downloader_size"] = display_dl_seed.size or 0
                        row_data["downloader_tracker"] = display_dl_seed.tracker or ""
                        row_data["downloader_tracker_domain"] = display_dl_seed.tracker_domain or ""
                        row_data["downloader_ratio"] = (display_dl_snap.ratio if display_dl_snap else None) or 0.0
                        row_data["downloader_uploaded"] = (display_dl_snap.uploaded if display_dl_snap else None) or 0
                        row_data["downloader_downloaded"] = (display_dl_snap.downloaded if display_dl_snap else None) or 0
                        row_data["downloader_seeding_time"] = (display_dl_snap.seeding_time if display_dl_snap else None) or 0
                        row_data["downloader_state"] = (display_dl_snap.downloader_status if display_dl_snap else None) or ""
                        row_data["downloader_status"] = row_data["downloader_state"]
                        row_data["downloader_save_path"] = (display_dl_snap.save_path if display_dl_snap else None) or ""
                        row_data["downloader_category"] = (display_dl_snap.category if display_dl_snap else None) or ""
                        row_data["downloader_tags"] = (display_dl_snap.tags if display_dl_snap else None) or ""
                        row_data["downloader_added_at"] = display_dl_seed.added_at
                        row_data["suggested_match"] = used_suggested

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
                dl_candidates_payload = [
                    {
                        "hash": dl_seed.downloader_hash,
                        "name": getattr(dl_seed, "downloader_torrent_name", None) or "",
                        "total_size": dl_seed.size or 0,
                        "ratio": (dl_snap.ratio if dl_snap else None) or 0.0,
                        "tracker": dl_seed.tracker or "",
                        "downloader": dl_seed.downloader_name or "",
                    }
                    for dl_seed, dl_snap in dl_candidates
                ]
                result.append({
                    "site_name": site.get("name") or domain_key,
                    "domain": domain_key,
                    "site_url": site_url,
                    "address_mappings": address_mappings,
                    "bonus_params": bonus_params,
                    "has_bonus_params": has_params,
                    "torrents": rows,
                    "total_bonus_per_hour": total_bonus,
                    "total_seed_count": len(rows),
                    "downloader_candidates": dl_candidates_payload,
                })
            except Exception as e:
                logger.error(f"PT魔力计算器插件：处理站点出错: {e}", exc_info=True)
                continue

        if self.sync_downloaders:
            try:
                raw_torrents = downloader_fetcher.fetch_downloader_torrents(self.sync_downloaders) or []
            except Exception as e:
                logger.debug(f"PT魔力计算器插件：拉取下载器种子失败: {e}")
                raw_torrents = []
            by_downloader: Dict[str, List[Dict[str, Any]]] = {}
            for dt in raw_torrents:
                dn = dt.get("downloader") or ""
                if dn:
                    by_downloader.setdefault(dn, []).append(dt)
            for downloader_name, list_dt in by_downloader.items():
                _attr = "main" if downloader_name in (self.primary_downloaders or []) else ("aux" if downloader_name in (self.aux_downloaders or []) else None)
                for _dt in list_dt:
                    _dt["seed_attr"] = _attr
                try:
                    seedinfo_oper.batch_upsert_downloader_torrents(None, downloader_name, list_dt)
                except Exception as e:
                    logger.debug(f"PT魔力计算器插件：批量写入下载器种子表失败 {downloader_name}: {e}")
        return result

    def _api_site_list(self, data: Optional[dict] = Body(None)) -> Dict[str, Any]:
        """仅返回已选站点列表及存储的种子总数、总时魔，供做种页表头展示，不拉取做种明细。"""
        try:
            sites_to_query = self._get_sites_to_query_from_plugindata(None)
            sites_list = []
            for s in sites_to_query:
                if not s.get("domain"):
                    continue
                domain = StringUtils.get_url_domain(s.get("domain") or "") or ""
                total_seed_count = int(s.get("total_seed_count", 0) or 0)
                total_bonus_per_hour = float(s.get("total_bonus_per_hour", 0) or 0)
                bonus_params = self.get_data(f"bonus_params_{domain}") or {}
                if not isinstance(bonus_params, dict):
                    bonus_params = {}
                has_bonus_params = bool(
                    bonus_params.get("T0") and bonus_params.get("N0")
                    and bonus_params.get("B0") and bonus_params.get("L")
                )
                sites_list.append({
                    "domain": domain,
                    "site_name": s.get("name") or (s.get("domain") or ""),
                    "site_url": (s.get("url") or "").strip().rstrip("/"),
                    "torrents": [],
                    "total_bonus_per_hour": total_bonus_per_hour,
                    "total_seed_count": total_seed_count,
                    "has_bonus_params": has_bonus_params,
                    "bonus_params": bonus_params,
                })
            return {"success": True, "sites": sites_list}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：site_list 失败: {e}", exc_info=True)
            return {"success": False, "message": str(e), "sites": []}

    def _api_bonus_data(self, data: Optional[dict] = Body(None)):
        """按 site_id 返回该站点的做种明细（左表行 + 右表下载器候选）。不做站点列表，站点列表请调 site_list。"""
        try:
            data = data or {}
            site_id = data.get("site_id")
            keyword = data.get("keyword")
            override = {}
            if "selected_sites" in data:
                override["selected_sites"] = data["selected_sites"]
            if "sync_downloaders" in data:
                override["sync_downloaders"] = data["sync_downloaders"]
            if "primary_downloaders" in data or "aux_downloaders" in data:
                prim = utils.parse_list_config(data.get("primary_downloaders"))
                aux = utils.parse_list_config(data.get("aux_downloaders"))
                override["sync_downloaders"] = list(dict.fromkeys(prim + aux))
            if "site_address_mappings" in data and isinstance(data["site_address_mappings"], dict):
                override["site_address_mappings"] = data["site_address_mappings"]
            payload = self._build_seed_association_payload(
                override_config=override if override else None,
                site_id=site_id,
                keyword=keyword,
            )
            sites_blocks = payload.get("sites", [])
            if site_id and sites_blocks:
                left_table = next(
                    (b.get("torrents", []) for b in sites_blocks if (b.get("domain") or "") == site_id),
                    sites_blocks[0].get("torrents", []) if sites_blocks else [],
                )
            else:
                left_table = []
                for block in sites_blocks:
                    for row in block.get("torrents", []):
                        left_table.append({**row, "site_domain": block.get("domain", "")})
            right_table = payload.get("downloader_torrents", []) if site_id else payload.get("downloader_torrents_by_site", {})
            return {"success": True, "left_table": left_table, "right_table": right_table}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：bonus_data 失败: {e}", exc_info=True)
            return {"success": False, "message": str(e), "left_table": [], "right_table": {}}

    def _downloader_torrents_list(self, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        raw_list = downloader_fetcher.fetch_downloader_torrents(self.sync_downloaders) or []
        result = []
        for torrent in raw_list:
            name = torrent.get("name", "")
            if keyword and keyword.lower() not in name.lower():
                continue
            result.append({"hash": torrent.get("hash", ""), "name": name, "size": torrent.get("total_size", 0), "ratio": torrent.get("ratio", 0.0), "downloader": torrent.get("downloader", "")})
        result.sort(key=lambda x: x.get("name", ""))
        return result

    def _get_torrent_mappings(self) -> Dict[str, str]:
        try:
            return seedinfo_oper.list_all_mappings(None, self.sync_downloaders)
        except Exception:
            return {}

    def _save_torrent_mapping(self, site_domain: str, torrent_key: str, downloader_hash: str, site_data: Optional[Dict[str, Any]] = None, downloader_data: Optional[Dict[str, Any]] = None):
        try:
            seedinfo_oper.save_torrent_mapping(
                None, site_domain, torrent_key, downloader_hash,
                self.sync_downloaders, self.primary_downloaders, self.aux_downloaders,
                site_data, downloader_data,
            )
        except Exception as e:
            logger.debug(f"PT魔力计算器插件：保存种子信息表失败: {e}")

    def _remove_torrent_mapping(self, site_domain: str, torrent_key: str):
        try:
            seedinfo_oper.remove_seed_info(None, site_domain, torrent_key)
        except Exception as e:
            logger.debug(f"PT魔力计算器插件：删除种子信息表记录失败: {e}")

    def _build_seed_association_payload(
        self,
        override_config: Optional[Dict[str, Any]] = None,
        site_id: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        backup = {"selected_sites": self.selected_sites, "sync_downloaders": self.sync_downloaders, "primary_downloaders": self.primary_downloaders, "aux_downloaders": self.aux_downloaders, "site_address_mappings": dict(self.site_address_mappings)}
        try:
            if override_config:
                if "selected_sites" in override_config:
                    self.selected_sites = utils.parse_list_config(override_config["selected_sites"])
                if "sync_downloaders" in override_config:
                    self.sync_downloaders = utils.parse_list_config(override_config["sync_downloaders"])
                elif "primary_downloaders" in override_config or "aux_downloaders" in override_config:
                    prim = utils.parse_list_config(override_config.get("primary_downloaders"))
                    aux = utils.parse_list_config(override_config.get("aux_downloaders"))
                    self.primary_downloaders = prim
                    self.aux_downloaders = aux
                    self.sync_downloaders = list(dict.fromkeys(prim + aux))
                if "site_address_mappings" in override_config and isinstance(override_config["site_address_mappings"], dict):
                    self.site_address_mappings = {k: (v if isinstance(v, list) else [str(v)]) for k, v in override_config["site_address_mappings"].items()}

            if not self.sites_helper:
                self.sites_helper = SitesHelper()
            unmatched_torrents_data = self._get_bonus_seeding_data(site_id, use_plugindata_only=True)
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
                            "torrent_key": utils.torrent_key(torrent.get("torrent_id"), torrent.get("name", ""), torrent.get("size", 0)),
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

            downloader_torrents_by_site = {}
            for block in unmatched_torrents_data:
                domain = block.get("domain", "")
                downloader_torrents_by_site[domain] = block.get("downloader_candidates", [])

            for site_domain, site_data in unmatched_by_site.items():
                site_list = downloader_torrents_by_site.get(site_domain, [])
                site_filtered_list = []
                for t in site_list:
                    name = t.get("name") or ""
                    size_str = StringUtils.str_filesize(t.get("total_size", 0))
                    suffix = f" ({size_str})"
                    max_name_width = 48 - utils.display_width(suffix)
                    short_name, _ = utils.truncate_by_display_width(name, max_name_width)
                    site_filtered_list.append({"display": short_name + suffix, "value": t.get("hash", ""), "title": name})
                site_filtered_list.sort(key=lambda x: x["display"])
                for unmatched in site_data["torrents"]:
                    unmatched["options"] = site_filtered_list

            if site_id and site_id in downloader_torrents_by_site:
                downloader_torrents_list = downloader_torrents_by_site[site_id]
                if keyword:
                    downloader_torrents_list = [
                        t for t in downloader_torrents_list
                        if keyword.lower() in (t.get("name") or "").lower()
                    ]
            else:
                downloader_torrents_list = []

            return {
                "sites": unmatched_torrents_data,
                "downloader_torrents": downloader_torrents_list,
                "downloader_torrents_by_site": downloader_torrents_by_site,
            }
        finally:
            self.selected_sites = backup["selected_sites"]
            self.sync_downloaders = backup["sync_downloaders"]
            self.primary_downloaders = backup["primary_downloaders"]
            self.aux_downloaders = backup["aux_downloaders"]
            self.site_address_mappings = backup["site_address_mappings"]

    def _api_save_data(self, data: dict = Body(...)):
        """根据前端传入的 associations 批量保存或删除关联"""
        try:
            associations = data.get("associations")
            if not isinstance(associations, list):
                return {"success": False, "message": "参数 associations 必须为列表"}
            for item in associations:
                if not isinstance(item, dict):
                    continue
                site_domain = item.get("site_domain")
                torrent_key = item.get("torrent_key")
                if not site_domain or not torrent_key:
                    continue
                downloader_hash = item.get("downloader_hash")
                if downloader_hash:
                    self._save_torrent_mapping(site_domain, torrent_key, str(downloader_hash).strip())
                else:
                    self._remove_torrent_mapping(site_domain, torrent_key)
            return {"success": True, "message": "保存成功"}
        except Exception as e:
            logger.error(f"PT魔力计算器插件：save_data 失败: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

