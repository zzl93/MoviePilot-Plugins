# -*- coding: utf-8 -*-
"""
同步做种与魔力参数到插件存储：torrent_activity -> ptbonuscalc_seedinfo，bonus_params -> PluginData。
在 SiteRefreshed 事件后，从 siteuserdata 读取并写入插件表；或通过 API/服务主动触发。
"""
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.cloudflare import under_challenge
from app.log import logger
from app.utils.http import RequestUtils
from app.utils.string import StringUtils

from app.plugins.ptbonuscalc.server import seedinfo_oper
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
            all_seeding.extend(detail_list)
            if not next_page:
                break
        mybonus_html = _fetch_page(urljoin(base_url + "/", "mybonus.php"), cookie=cookie, ua=ua, proxy=proxy)
        bonus_params = parse_bonus_params_nexusphp(mybonus_html, site_name=site_name)
        seedinfo_oper.batch_save_seeding_from_parser(None, domain_key, all_seeding)
        plugin_instance.save_data(f"bonus_params_{domain_key}", bonus_params)
        return True
    except Exception as e:
        logger.warning(f"PT魔力计算器插件：拉取解析同步失败 {domain_key}: {e}", exc_info=True)
        return False
