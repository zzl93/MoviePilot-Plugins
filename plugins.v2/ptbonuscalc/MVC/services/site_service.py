"""
站点数据：从主项目取站、同步做种数据。下层仅拉取并返回数据，上层负责持久化。
站点刷新时请求 mybonus.php（魔力公式与参数）和 getusertorrentlistajax.php（做种页种子信息）。
bonus_params 优先从站点配置 json 读取，空则从 mybonus 解析并写入 json；快照表不存 bonus_params。PluginData 仅存 site_fully_matched。
"""
from typing import List, Optional, Any
from app.log import logger
from app.db.site_oper import SiteOper
from app.utils.string import StringUtils
from app.helper.sites import SitesHelper
from app.plugins.ptbonuscalc.MVC.mappers.site_seed_mapper import SiteSeedMapper as SSM
from app.plugins.ptbonuscalc.MVC.utils import (
    parse_torrent_activity_nexusphp,
    parse_bonus_params_nexusphp,
    calc_bonus_per_hour,
)
from app.plugins.ptbonuscalc.MVC.utils.site_config_loader import get_site_parser_config, save_bonus_params_for_domain
from app.plugins.ptbonuscalc.MVC.utils.page_parser import extract_next_seeding_page_url
from app.utils.http import RequestUtils


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
    """从 indexer + DB + site_config 构建站点列表（含 total_seed_count、total_bonus_per_hour、bonus_params）。不再从 PluginData 读 site_info。"""
    indexers = get_sites_to_query(plugin, site_id=site_id)
    mapper = SSM()
    result = []
    for idx in indexers:
        domain = (idx.get("domain") or "").strip()
        if not domain:
            continue
        site_config, _ = get_site_parser_config(domain)
        bonus_params = (site_config or {}).get("bonus_params") or {}
        bonus_params = bonus_params if isinstance(bonus_params, dict) else {}
        sid = idx.get("id")
        pairs = mapper.list_seed_with_latest_snapshot_by_site(site_id=sid) if sid is not None else []
        total_seed_count = 0
        total_bonus_per_hour = 0.0
        for item in pairs:
            site_seed = item.get("site_seed")
            site_snap = item.get("site_snapshot")
            if not site_seed:
                continue
            total_seed_count += 1
            bp = (site_snap.bonus_per_hour or 0.0) if site_snap else 0.0
            if bp == 0 and bonus_params and site_snap:
                bp = calc_bonus_per_hour(
                    site_snap.size or 0,
                    site_snap.seed_time or 0,
                    bonus_params,
                    seeders=(site_snap.extra or {}).get("seeders") if isinstance(site_snap.extra, dict) else None,
                    weight=(site_snap.extra or {}).get("weight") if isinstance(site_snap.extra, dict) else None,
                )
            total_bonus_per_hour += bp
        result.append({
            "id": sid,
            "name": idx.get("name"),
            "domain": domain,
            "url": idx.get("domain"),
            "schema": idx.get("parser") or "NexusPHP",
            "total_seed_count": total_seed_count,
            "total_bonus_per_hour": round(total_bonus_per_hour, 2),
            "bonus_params": bonus_params,
            "has_bonus_params": bool(bonus_params),
        })
    return result


def _has_valid_bonus_params(params: dict) -> bool:
    """bonus_params 需包含 T0/N0/B0/L 才算有效。"""
    if not params or not isinstance(params, dict):
        return False
    for k in ("T0", "N0", "B0", "L"):
        if not params.get(k):
            return False
    return True


def fetch_bonus_params(domain: str, base_url: str, req: RequestUtils, site_config: dict, is_configured: bool) -> dict:
    """
    请求 mybonus.php 获取魔力公式参数。优先从站点配置 json 读取，为空则请求并解析。
    返回 bonus_params dict，无效时返回 {}。
    """
    bonus_params = (site_config or {}).get("bonus_params") or {}
    if not isinstance(bonus_params, dict):
        bonus_params = {}
    if _has_valid_bonus_params(bonus_params):
        return bonus_params
    try:
        bonus_res = req.get_res(f"{base_url}/mybonus.php")
        bonus_html = (bonus_res.text if bonus_res and getattr(bonus_res, "status_code", 0) == 200 else "") or ""
        logger.info(f"[ptbonuscalc] fetch_bonus_params 请求 mybonus.php html_len={len(bonus_html)}")
    except Exception as e:
        logger.warning(f"[ptbonuscalc] fetch_bonus_params mybonus.php 请求异常: {e}")
        return bonus_params
    bonus_params = parse_bonus_params_nexusphp(bonus_html) or {}
    if _has_valid_bonus_params(bonus_params) and is_configured:
        save_bonus_params_for_domain(domain, bonus_params)
    return bonus_params


def fetch_seeding_torrents(
    base_url: str, userid: str, site_config: dict, req: RequestUtils
) -> tuple[List[dict], Optional[str]]:
    """
    请求 getusertorrentlistajax.php（分页）获取做种列表并解析。
    返回 (valid_torrents, error)。error 为 None 表示成功；非 None 为请求异常信息。
    """
    current_url = f"{base_url}/getusertorrentlistajax.php?userid={userid}&type=seeding"
    all_torrents = []
    page_num = 0
    while current_url:
        page_num += 1
        try:
            res = req.get_res(current_url)
            status = getattr(res, "status_code", None)
            html = (res.text if res and status == 200 else "") or ""
            logger.info(f"[ptbonuscalc] fetch_seeding_torrents page={page_num} url={current_url} status={status} html_len={len(html)}")
        except Exception as e:
            logger.warning(f"[ptbonuscalc] fetch_seeding_torrents 请求异常: {e}")
            return [], str(e)
        if not html.strip():
            logger.info(f"[ptbonuscalc] fetch_seeding_torrents page={page_num} html 为空，结束拉取")
            break
        parser_result = parse_torrent_activity_nexusphp(html, site_config=site_config)
        page_torrents = parser_result.get("torrents") or []
        for t in page_torrents:
            tid = t.get("torrent_id")
            if tid and tid not in {x.get("torrent_id") for x in all_torrents}:
                all_torrents.append(t)
        next_url = extract_next_seeding_page_url(html, base_url, current_url=current_url)
        if next_url and next_url != current_url and "getusertorrentlistajax" in next_url:
            current_url = next_url
        else:
            current_url = None
    valid_torrents = [t for t in all_torrents if t.get("torrent_id") and str(t.get("torrent_id")).isdigit()]
    return valid_torrents, None


def fetch_site_seeding_data(site: dict, plugin) -> dict:
    """
    下层：调用 fetch_bonus_params 和 fetch_seeding_torrents，组合返回结构化数据，不写 DB 不写 PluginData。
    返回 { success, site_id, domain, valid_torrents, bonus_params, total_seed_count, total_bonus_per_hour, error?, skip_reason? }
    """
    site_id = site.get("id")
    domain = site.get("domain") or site.get("url") or ""
    out = {
        "success": False,
        "site_id": site_id,
        "domain": domain,
        "valid_torrents": [],
        "bonus_params": {},
        "total_seed_count": 0,
        "total_bonus_per_hour": 0.0,
        "error": None,
        "skip_reason": None,
    }
    if not domain or site_id is None:
        out["skip_reason"] = "domain 或 site_id 为空"
        return out
    schema = (site.get("schema") or site.get("parser") or "").lower()
    if "nexusphp" not in schema and "nexus" not in schema:
        out["skip_reason"] = f"非 NexusPHP schema={schema}"
        return out
    domain_key = StringUtils.get_url_domain(domain)
    ud_list = SiteOper().get_userdata_by_domain(domain_key) or []
    userid = None
    if ud_list:
        def _ud_key(u):
            d = getattr(u, "updated_day", None) or (u.get("updated_day") if isinstance(u, dict) else None) or ""
            t = getattr(u, "updated_time", None) or (u.get("updated_time") if isinstance(u, dict) else None) or ""
            return (d, t)
        latest = max(ud_list, key=_ud_key)
        userid = getattr(latest, "userid", None) or (latest.get("userid") if isinstance(latest, dict) else None)
        if userid is not None:
            userid = str(userid).strip() or None
    if not userid:
        out["skip_reason"] = "未获取到 userid，仅支持 getusertorrentlistajax"
        return out
    base_url = domain.rstrip("/")
    cookie = site.get("cookie")
    ua = site.get("ua") or site.get("user_agent")
    headers = {"User-Agent": ua} if ua else {}
    req = RequestUtils(cookies=cookie, headers=headers, timeout=30)
    site_config, is_configured = get_site_parser_config(domain)

    bonus_params = fetch_bonus_params(domain, base_url, req, site_config, is_configured)
    valid_torrents, err = fetch_seeding_torrents(base_url, userid, site_config, req)
    if err:
        out["error"] = err
        return out
    if not is_configured and len(valid_torrents) == 0:
        out["skip_reason"] = "站点无专属配置且解析不到种子ID"
        return out
    if bonus_params:
        for torrent in valid_torrents:
            extra = torrent.get("extra") or {}
            torrent["bonus_per_hour"] = calc_bonus_per_hour(
                size_bytes=torrent.get("size") or 0,
                seed_time_seconds=torrent.get("seed_time") or 0,
                bonus_params=bonus_params,
                seeders=extra.get("seeders"),
                weight=extra.get("weight"),
            )
    total_bonus_per_hour = sum((t.get("bonus_per_hour") or 0.0) for t in valid_torrents)
    out["success"] = True
    out["valid_torrents"] = valid_torrents
    out["bonus_params"] = bonus_params
    out["total_seed_count"] = len(valid_torrents)
    out["total_bonus_per_hour"] = round(total_bonus_per_hour, 2)
    logger.info(
        f"[ptbonuscalc] fetch_site_seeding_data 完成 site_id={site_id} "
        f"valid_torrents={len(valid_torrents)} total_bonus_per_hour={out['total_bonus_per_hour']}"
    )
    return out


def trigger_sync_on_site_refresh(plugin, event) -> None:
    """上层：根据 event 得到 site_id，调用 fetch 获取数据，主表更新、快照表新增，不删除历史数据。不写 PluginData。"""
    site_id = None
    payload = getattr(event, "event_data", None) or getattr(event, "data", None)
    if isinstance(payload, dict):
        site_id = payload.get("site_id") or payload.get("id")
    logger.info(f"[ptbonuscalc] trigger_sync site_id={site_id}")
    if site_id is None:
        logger.warning("[ptbonuscalc] trigger_sync 未解析到 site_id，跳过")
        return
    sites_helper = SitesHelper()
    site_oper = SiteOper()
    mapper = SSM()
    if site_id == "*":
        indexers = get_sites_to_query(plugin, site_id=None, filter_by_selected_sites=True)
        logger.info(f"[ptbonuscalc] trigger_sync site_id=* 已选站点数={len(indexers)}")
        for indexer in indexers:
            schema = (indexer.get("parser") or indexer.get("schema") or "").lower()
            if "nexusphp" not in schema and "nexus" not in schema:
                continue
            result = fetch_site_seeding_data(indexer, plugin)
            if result.get("skip_reason"):
                logger.debug(f"[ptbonuscalc] trigger_sync 跳过 id={indexer.get('id')} {result['skip_reason']}")
                continue
            if not result.get("success"):
                logger.warning(f"[ptbonuscalc] trigger_sync 拉取失败 id={indexer.get('id')} error={result.get('error')}")
                continue
            parser_result = {"torrents": result["valid_torrents"], "bonus_params": result["bonus_params"]}
            mapper.batch_save_seeding_from_parser(site_id=result["site_id"], parser_result=parser_result)
            logger.info(f"[ptbonuscalc] trigger_sync 已写入 SiteSeed/Snapshot site_id={result['site_id']} 种子数={result['total_seed_count']}")
        return
    site = site_oper.get(site_id)
    if not site:
        logger.warning(f"[ptbonuscalc] trigger_sync 未找到站点 site_id={site_id}")
        return
    indexers = sites_helper.get_indexers() or []
    indexer = next((x for x in indexers if x.get("id") == site_id), None) or sites_helper.get_indexer(site.domain)
    if not indexer:
        logger.warning(f"[ptbonuscalc] trigger_sync 未获取到 indexer site_id={site_id}")
        return
    schema = (indexer.get("parser") or indexer.get("schema") or "").lower()
    if "nexusphp" not in schema and "nexus" not in schema:
        logger.info(f"[ptbonuscalc] trigger_sync 跳过非 NexusPHP id={site_id}")
        return
    result = fetch_site_seeding_data(indexer, plugin)
    if result.get("skip_reason"):
        logger.info(f"[ptbonuscalc] trigger_sync 跳过 {result['skip_reason']}")
        return
    if not result.get("success"):
        logger.warning(f"[ptbonuscalc] trigger_sync 拉取失败 error={result.get('error')}")
        return
    parser_result = {"torrents": result["valid_torrents"], "bonus_params": result["bonus_params"]}
    mapper.batch_save_seeding_from_parser(site_id=result["site_id"], parser_result=parser_result)
    logger.info(f"[ptbonuscalc] trigger_sync 已写入 SiteSeed/Snapshot site_id={result['site_id']} 种子数={result['total_seed_count']}")
