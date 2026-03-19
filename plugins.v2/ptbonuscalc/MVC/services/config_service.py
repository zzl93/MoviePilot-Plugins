"""
配置与选项：表单站点/下载器选项、按下载器拉取 Tracker 选项。
"""
from typing import List, Any
from app.helper.sites import SitesHelper
from app.helper.service import ServiceConfigHelper
from app.schemas import DownloaderConf
from app.schemas.types import SystemConfigKey
from app.plugins.ptbonuscalc.MVC.utils.downloader_fetcher import fetch_downloader_torrents
from app.plugins.ptbonuscalc.MVC.utils.tracker import parse_tracker_domain, keyword_to_domain


def get_form_options(plugin) -> dict:
    """从主项目取已启用站点与下载器，转为 title/value；从 site_address_mappings 汇总地址关键词经 keyword_to_domain 得 address_keyword_options。"""
    sites_helper = SitesHelper()
    indexers = sites_helper.get_indexers() or []
    sites = [{"title": x.get("name") or x.get("domain"), "value": x.get("id")} for x in indexers if x.get("is_active")]
    configs = ServiceConfigHelper.get_configs(SystemConfigKey.Downloaders, DownloaderConf)
    downloaders = [{"title": c.name, "value": c.name} for c in (configs or []) if c.enabled and c.name]
    mappings = getattr(plugin, "site_address_mappings", None) or {}
    keywords = set()
    for v in mappings.values():
        if isinstance(v, list):
            keywords.update(k for k in v if k)
        elif isinstance(v, str) and v:
            keywords.add(v)
    address_keyword_options = []
    for k in sorted(keywords):
        domain = keyword_to_domain(k)
        address_keyword_options.append({"title": k, "value": domain or k})
    return {
        "sites": sites,
        "downloaders": downloaders,
        "address_keyword_options": address_keyword_options,
        "suggested_site_mappings": {},
    }


def get_downloader_tracker_options(plugin, data: dict) -> dict:
    """从 data 解析下载器列表，拉取种子，从 tracker 提域名去重排序，返回 address_keyword_options。"""
    downloader_names = data.get("downloaders") or data.get("downloader_list") or []
    if isinstance(downloader_names, str):
        downloader_names = [downloader_names]
    domains = set()
    for name in downloader_names:
        if not name:
            continue
        torrents = fetch_downloader_torrents(name)
        for t in torrents:
            tracker = t.get("tracker")
            d = parse_tracker_domain(tracker)
            if d:
                domains.add(d)
    address_keyword_options = [{"title": d, "value": d} for d in sorted(domains)]
    return {"address_keyword_options": address_keyword_options}
