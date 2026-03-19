"""
站点数据：从主项目取站、写 PluginData、同步做种数据（拉取→解析→写表与 PluginData）。
"""
from typing import List, Optional, Any
from app.log import logger
from app.db.site_oper import SiteOper
from app.db.plugindata_oper import PluginDataOper
from app.helper.sites import SitesHelper
from app.plugins.ptbonuscalc.MVC.mappers.site_seed_mapper import SiteSeedMapper as SSM
from app.plugins.ptbonuscalc.MVC.utils import (
    parse_torrent_activity_nexusphp,
    parse_bonus_params_nexusphp,
    calc_bonus_per_hour,
)
from app.utils.http import RequestUtils
import json


def _plugin_id(plugin) -> str:
    return getattr(plugin, "plugin_id", None) or type(plugin).__name__


def _selected_sites(plugin) -> List[int]:
    s = getattr(plugin, "selected_sites", None) or []
    return s if isinstance(s, list) else []


def get_sites_to_query(
    plugin,
    site_id: Optional[int] = None,
    filter_by_selected_sites: bool = True,
) -> List[dict]:
    """若传 site_id 则从主项目站点表查该站 indexer 返回单元素列表；否则取主项目已启用站点并按 selected_sites 过滤。"""
    sites_helper = SitesHelper()
    if site_id is not None:
        site_oper = SiteOper()
        site = site_oper.get(site_id)
        if not site:
            return []
        indexer = sites_helper.get_indexer(site.domain)
        return [indexer] if indexer else []
    indexers = sites_helper.get_indexers() or []
    if not indexers:
        return []
    if filter_by_selected_sites:
        selected = _selected_sites(plugin)
        if selected:
            indexers = [x for x in indexers if x.get("id") in selected]
    return [x for x in indexers if x.get("is_active")]


def get_sites_from_plugindata(plugin, site_id: Optional[int] = None) -> List[dict]:
    """从 PluginData 读 site_info_{domain}，可传 site_id 筛单站。返回项含 total_seed_count、total_bonus_per_hour、bonus_params、has_bonus_params 等。"""
    pid = _plugin_id(plugin)
    pdo = PluginDataOper()
    all_data = pdo.get_data_all(pid) or []
    site_infos = []
    filter_domain = None
    if site_id is not None:
        site_oper = SiteOper()
        site = site_oper.get(site_id)
        if site:
            filter_domain = site.domain
    for row in all_data:
        key = getattr(row, "key", None) or row.get("key") if isinstance(row, dict) else None
        if not key or not str(key).startswith("site_info_"):
            continue
        domain = str(key).replace("site_info_", "", 1)
        if filter_domain is not None and domain != filter_domain:
            continue
        val = getattr(row, "value", None) or (row.get("value") if isinstance(row, dict) else None)
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                val = {}
        if not isinstance(val, dict):
            val = {}
        bonus_key = f"bonus_params_{domain}"
        bonus_raw = pdo.get_data(pid, bonus_key)
        if isinstance(bonus_raw, str):
            try:
                bonus_params = json.loads(bonus_raw)
            except Exception:
                bonus_params = {}
        else:
            bonus_params = bonus_raw or {}
        val["bonus_params"] = bonus_params
        val["has_bonus_params"] = bool(bonus_params)
        site_infos.append(val)
    return site_infos


def write_site_infos_for_selected(plugin) -> None:
    """按 selected_sites 从主项目取各站 indexer，写入 PluginData 的 site_info_{domain}，并合并已存的 total_*。"""
    pid = _plugin_id(plugin)
    pdo = PluginDataOper()
    sites = get_sites_to_query(plugin, filter_by_selected_sites=True)
    for indexer in sites:
        domain = (indexer.get("domain") or "").strip()
        if not domain:
            continue
        key = f"site_info_{domain}"
        existing = pdo.get_data(pid, key)
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except Exception:
                existing = {}
        if not isinstance(existing, dict):
            existing = {}
        info = {
            "id": indexer.get("id"),
            "name": indexer.get("name"),
            "domain": domain,
            "url": indexer.get("domain"),
            "schema": indexer.get("parser") or "NexusPHP",
        }
        info["total_seed_count"] = existing.get("total_seed_count")
        info["total_bonus_per_hour"] = existing.get("total_bonus_per_hour")
        pdo.save(pid, key, info)


def sync_site_seeding_data(site: dict, plugin) -> None:
    """从站点拉取做种数据的完整流程：发 HTTP 请求拉取做种页与 mybonus → 解析 → 写站点种子表 → 写/更新 PluginData。"""
    pid = _plugin_id(plugin)
    site_id = site.get("id")
    domain = site.get("domain") or site.get("url") or ""
    logger.info(f"[ptbonuscalc] sync_site_seeding_data 开始 site_id={site_id} domain={domain}")
    if not domain or site_id is None:
        logger.warning(f"[ptbonuscalc] sync_site_seeding_data 跳过：domain 或 site_id 为空")
        return
    schema = (site.get("schema") or site.get("parser") or "").lower()
    if "nexusphp" not in schema and "nexus" not in schema:
        logger.debug(f"[ptbonuscalc] sync_site_seeding_data 跳过：非 NexusPHP schema={schema}")
        return
    base_url = domain.rstrip("/")
    cookie = site.get("cookie")
    ua = site.get("ua") or site.get("user_agent")
    headers = {"User-Agent": ua} if ua else {}
    # 先试 torrents.php（做种页），做种页面通常有完整表格
    torrents_url = f"{base_url}/torrents.php?type=seeding"
    ajax_url = f"{base_url}/getusertorrentlistajax.php?type=seeding"
    html = ""
    try:
        res = RequestUtils(cookies=cookie, headers=headers, timeout=30).get_res(torrents_url)
        html = (res.text if res and res.status_code == 200 else "") or ""
        logger.info(f"[ptbonuscalc] sync_site_seeding_data 做种页 url={torrents_url} status={getattr(res, 'status_code', None)} html_len={len(html)}")
        if html:
            preview = (html[:800] + "...") if len(html) > 800 else html
            logger.info(f"[ptbonuscalc] sync_site_seeding_data 做种页内容预览(前800字): {repr(preview)}")
    except Exception as e:
        logger.warning(f"[ptbonuscalc] sync_site_seeding_data 做种页请求异常: {e}")
    if not html.strip():
        try:
            res = RequestUtils(cookies=cookie, headers=headers, timeout=30).get_res(ajax_url)
            html = (res.text if res and res.status_code == 200 else "") or ""
            logger.info(f"[ptbonuscalc] sync_site_seeding_data 备用 ajax url={ajax_url} status={getattr(res, 'status_code', None)} html_len={len(html)}")
            if html:
                preview = (html[:800] + "...") if len(html) > 800 else html
                logger.info(f"[ptbonuscalc] sync_site_seeding_data ajax 内容预览(前800字): {repr(preview)}")
        except Exception as e:
            logger.warning(f"[ptbonuscalc] sync_site_seeding_data 备用请求异常: {e}")
    bonus_html = ""
    try:
        bonus_res = RequestUtils(cookies=cookie, headers=headers, timeout=30).get_res(f"{base_url}/mybonus.php")
        bonus_html = (bonus_res.text if bonus_res and bonus_res.status_code == 200 else "") or ""
    except Exception:
        pass
    bonus_params = parse_bonus_params_nexusphp(bonus_html)
    from app.plugins.ptbonuscalc.MVC.utils.site_config_loader import get_site_parser_config
    site_config = get_site_parser_config(domain)
    parser_result = parse_torrent_activity_nexusphp(html, site_config=site_config)
    torrents_raw = parser_result.get("torrents") or []
    torrent_count = len(torrents_raw)
    has_torrent_id = sum(1 for t in torrents_raw if t.get("torrent_id") or t.get("id"))
    logger.info(f"[ptbonuscalc] sync_site_seeding_data 解析结果 torrents={torrent_count} 有效torrent_id={has_torrent_id} bonus_params_keys={list(bonus_params.keys()) if bonus_params else []}")
    if bonus_params is None:
        bonus_params = {}
    parser_result["bonus_params"] = bonus_params
    if bonus_params:
        for torrent in parser_result.get("torrents") or []:
            extra = torrent.get("extra") or {}
            torrent["bonus_per_hour"] = calc_bonus_per_hour(
                size_bytes=torrent.get("size") or 0,
                seed_time_seconds=torrent.get("seed_time") or 0,
                bonus_params=bonus_params,
                seeders=extra.get("seeders"),
                weight=extra.get("weight"),
            )
    mapper = SSM()
    mapper.batch_save_seeding_from_parser(site_id=site_id, parser_result=parser_result)
    torrents = parser_result.get("torrents") or []
    logger.info(f"[ptbonuscalc] sync_site_seeding_data 已写入 SiteSeed/Snapshot site_id={site_id} 种子数={len(torrents)}")
    total_seed_count = len(torrents)
    total_bonus_per_hour = sum((t.get("bonus_per_hour") or 0.0) for t in torrents)
    bonus_decimals = int(bonus_params.get("hourly_bonus_decimals") or 2) if bonus_params else 2
    key = f"site_info_{domain}"
    pdo = PluginDataOper()
    existing = pdo.get_data(pid, key)
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except Exception:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["total_seed_count"] = total_seed_count
    existing["total_bonus_per_hour"] = round(total_bonus_per_hour, bonus_decimals)
    existing["name"] = existing.get("name") or site.get("name")
    existing["domain"] = domain
    pdo.save(pid, key, existing)
    if bonus_params is not None:
        pdo.save(pid, f"bonus_params_{domain}", bonus_params)


def trigger_sync_on_site_refresh(plugin, event) -> None:
    """根据 event 得到 site_id，从主项目取对应站点信息，若为 NexusPHP 则调 sync_site_seeding_data。site_id 为 '*' 时对已选站点逐站同步。"""
    site_id = None
    payload = getattr(event, "event_data", None) or getattr(event, "data", None)
    if isinstance(payload, dict):
        site_id = payload.get("site_id") or payload.get("id")
    logger.info(f"[ptbonuscalc] trigger_sync site_id={site_id} (type={type(site_id).__name__})")
    if site_id is None:
        logger.warning("[ptbonuscalc] trigger_sync 未解析到 site_id，跳过")
        return
    sites_helper = SitesHelper()
    site_oper = SiteOper()
    if site_id == "*":
        indexers = get_sites_to_query(plugin, site_id=None, filter_by_selected_sites=True)
        logger.info(f"[ptbonuscalc] trigger_sync site_id=* 已选站点数={len(indexers)}, selected_sites={getattr(plugin, 'selected_sites', None)}")
        for indexer in indexers:
            schema = (indexer.get("parser") or indexer.get("schema") or "").lower()
            if "nexusphp" in schema or "nexus" in schema:
                logger.info(f"[ptbonuscalc] trigger_sync 同步站点 id={indexer.get('id')} domain={indexer.get('domain')} schema={schema}")
                sync_site_seeding_data(indexer, plugin)
            else:
                logger.debug(f"[ptbonuscalc] trigger_sync 跳过非 NexusPHP 站点 id={indexer.get('id')} schema={schema}")
        return
    site = site_oper.get(site_id)
    if not site:
        logger.warning(f"[ptbonuscalc] trigger_sync 未找到站点 site_id={site_id}")
        return
    # 使用 get_indexers 按 id 取，因 get_indexer(domain) 可能不包含 schema
    indexers = sites_helper.get_indexers() or []
    indexer = next((x for x in indexers if x.get("id") == site_id), None)
    if not indexer:
        indexer = sites_helper.get_indexer(site.domain)
    if not indexer:
        logger.warning(f"[ptbonuscalc] trigger_sync 未获取到 indexer site_id={site_id} domain={site.domain}")
        return
    schema = (indexer.get("parser") or indexer.get("schema") or "").lower()
    logger.info(f"[ptbonuscalc] trigger_sync indexer schema={schema} parser={indexer.get('parser')}")
    if "nexusphp" not in schema and "nexus" not in schema:
        logger.info(f"[ptbonuscalc] trigger_sync 跳过非 NexusPHP 站点 id={site_id} schema={schema}")
        return
    logger.info(f"[ptbonuscalc] trigger_sync 同步单站 id={site_id} domain={indexer.get('domain')}")
    sync_site_seeding_data(indexer, plugin)
