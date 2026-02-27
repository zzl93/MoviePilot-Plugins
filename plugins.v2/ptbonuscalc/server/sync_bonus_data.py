# -*- coding: utf-8 -*-
"""
同步做种与魔力参数到插件存储：torrent_activity -> ptbonuscalc_seedinfo，bonus_params -> PluginData。
在 SiteRefreshed 事件后，从 siteuserdata 读取并写入插件表；或通过 API/服务主动触发。
"""
import math
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.cloudflare import under_challenge
from app.log import logger
from app.utils.http import RequestUtils
from app.utils.string import StringUtils

from app.plugins.ptbonuscalc.server import seedinfo_oper
from app.plugins.ptbonuscalc.server import utils as ptbonuscalc_utils
from app.plugins.ptbonuscalc.server.page_parser import (
    parse_bonus_params_nexusphp,
    parse_torrent_activity_nexusphp,
    extract_userid_from_index_nexusphp,
)


def _fetch_page(
    url: str,
    cookie: Optional[str] = None,
    params: Optional[dict] = None,
    ua: Optional[str] = None,
    proxy: Optional[bool] = None,
) -> str:
    """请求页面，返回 HTML 文本"""
    req_headers = {"User-Agent": ua or ""} if ua else {}
    if not req_headers:
        req_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    proxies = settings.PROXY if proxy else None
    if params:
        res = RequestUtils(
            cookies=cookie,
            timeout=60,
            proxies=proxies,
            headers=req_headers,
        ).post_res(url=url, data=params)
    else:
        res = RequestUtils(
            cookies=cookie,
            timeout=60,
            proxies=proxies,
            headers=req_headers,
        ).get_res(url=url)
    if res is None or res.status_code not in (200, 500, 403):
        return ""
    if under_challenge(res.text):
        return ""
    return RequestUtils.get_decoded_html_content(
        res,
        settings.ENCODING_DETECTION_PERFORMANCE_MODE,
        settings.ENCODING_DETECTION_MIN_CONFIDENCE,
    ) or ""

def sync_from_fetch(
    site: dict,
    plugin_instance: Any,
) -> bool:
    """
    主动拉取页面、解析并写入插件存储。适用于 NexusPHP 站点。
    :param site: 站点 dict，含 name, url, cookie, schema, ua, proxy
    :param plugin_instance: 插件实例
    :return: 是否成功
    """
    if site.get("schema") != "NexusPhp":
        return False
    url = site.get("url") or ""
    cookie = site.get("cookie") or ""
    ua = site.get("ua") or ""
    proxy = site.get("proxy")
    site_name = site.get("name") or ""
    domain_key = StringUtils.get_url_domain(url) or ""
    if not url or not domain_key:
        return False
    base_url = url.rstrip("/")
    try:
        index_html = _fetch_page(url, cookie=cookie, ua=ua, proxy=proxy)
        if not index_html:
            return False
        userid = extract_userid_from_index_nexusphp(index_html)
        if not userid:
            return False
        seeding_page = urljoin(base_url + "/", f"getusertorrentlistajax.php?userid={userid}&type=seeding")
        all_seeding = []
        next_page = None
        for _ in range(100):
            if next_page:
                page_url = urljoin(base_url + "/", next_page) if not str(next_page).startswith("http") else next_page
            else:
                page_url = seeding_page
            html = _fetch_page(page_url, cookie=cookie, ua=ua, proxy=proxy)
            if not html:
                break
            detail_list, next_page = parse_torrent_activity_nexusphp(html, site_name=site_name, multi_page=bool(next_page))
            for d in detail_list:
                if d.get("detail_path"):
                    d["detail_url"] = urljoin(base_url + "/", d["detail_path"])
            all_seeding.extend(detail_list)
            if not next_page:
                break
        mybonus_html = _fetch_page(urljoin(base_url + "/", "mybonus.php"), cookie=cookie, ua=ua, proxy=proxy)
        bonus_params = parse_bonus_params_nexusphp(mybonus_html, site_name=site_name)

        total_seed_count = len(all_seeding)
        total_bonus_per_hour = 0.0
        try:
            T0 = int(bonus_params.get("T0") or 0)
            N0 = int(bonus_params.get("N0") or 0)
            B0 = int(bonus_params.get("B0") or 0)
            L = int(bonus_params.get("L") or 0)
            has_params = all((T0, N0, B0, L))
            decimals = int(bonus_params.get("hourly_bonus_decimals") or 2)
            default_weight = float(bonus_params.get("default_weight") or 1.0)
            total_A = 0.0
            for s in all_seeding:
                seeders = int(s.get("seeders") or 0)
                size_b = int(s.get("size") or 0)
                S_GB = size_b / (1024 ** 3) if size_b else 0.0
                T_weeks = ptbonuscalc_utils.parse_pubdate_weeks(s.get("pubdate")) or 0.0
                weight = float(s.get("weight") or default_weight or 1.0)
                if has_params:
                    _, A_value, _ = ptbonuscalc_utils.calc_bonus_per_hour(T_weeks, S_GB, seeders, T0, N0, B0, L, weight)
                    total_A += A_value
            total_A = math.floor(total_A * 10) / 10
            if has_params and total_A > 0:
                total_bonus_per_hour = B0 * (2.0 / math.pi) * math.atan(total_A / L)
            seed_bonus = float(bonus_params.get("seeding_bonus_per_seed") or 0)
            seed_cap = int(bonus_params.get("seeding_bonus_cap") or 0)
            if seed_bonus > 0 and seed_cap > 0:
                total_bonus_per_hour += seed_bonus * min(total_seed_count, seed_cap)
            total_bonus_per_hour = math.floor(total_bonus_per_hour * (10 ** decimals)) / (10 ** decimals)
        except Exception as e:
            logger.debug(f"PT魔力计算器插件：同步时计算总时魔失败 {domain_key}: {e}")

        seedinfo_oper.batch_save_seeding_from_parser(None, domain_key, all_seeding)
        plugin_instance.save_data(f"bonus_params_{domain_key}", bonus_params)
        existing = plugin_instance.get_data(f"site_info_{domain_key}") or {}
        if not isinstance(existing, dict):
            existing = {}
        if all_seeding:
            existing["total_seed_count"] = total_seed_count
            existing["total_bonus_per_hour"] = total_bonus_per_hour
        elif existing.get("total_seed_count", 0) or existing.get("total_bonus_per_hour", 0):
            pass
        plugin_instance.save_data(
            f"site_info_{domain_key}",
            {
                **existing,
                "name": site_name,
                "domain": domain_key,
                "url": base_url,
                "schema": "NexusPhp",
            },
        )
        return True
    except Exception as e:
        logger.warning(f"PT魔力计算器插件：拉取解析同步失败 {domain_key}: {e}", exc_info=True)
        return False
