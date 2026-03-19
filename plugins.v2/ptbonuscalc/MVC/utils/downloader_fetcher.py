"""
从下载器拉取种子列表，供 config_service.get_downloader_tracker_options 等调用。
若插件内实现不足可依赖主项目 DownloaderHelper / 模块实例。
"""
from typing import Optional, List, Dict, Any

from app.helper.downloader import DownloaderHelper


def get_downloader_instance(downloader_name: Optional[str] = None):
    """根据名称获取下载器实例（qBittorrent/Transmission）。"""
    if not downloader_name:
        return None
    helper = DownloaderHelper()
    service = helper.get_service(name=downloader_name)
    if not service or not service.instance:
        return None
    return service.instance


def fetch_downloader_torrents(downloader_name: str) -> List[Dict[str, Any]]:
    """拉取指定下载器的种子列表，返回 [{ hash, name, total_size, ratio, tracker }, ...]。"""
    instance = get_downloader_instance(downloader_name)
    if not instance:
        return []
    out = []
    try:
        if hasattr(instance, "get_torrents"):
            torrents, err = instance.get_torrents()
            if err or not torrents:
                return out
            for t in torrents:
                h = t.get("hash") or t.get("id")
                if not h:
                    continue
                tracker = ""
                if isinstance(t.get("tracker"), str):
                    tracker = t.get("tracker", "")
                elif isinstance(t.get("trackers"), list) and t["trackers"]:
                    tracker = t["trackers"][0] if isinstance(t["trackers"][0], str) else t["trackers"][0].get("url", "")
                out.append({
                    "hash": h,
                    "info_hash": h,
                    "name": t.get("name") or t.get("title") or "",
                    "total_size": t.get("size") or t.get("total_size") or 0,
                    "size": t.get("size") or t.get("total_size") or 0,
                    "ratio": float(t.get("ratio") or 0),
                    "state": t.get("state") or "",
                    "tracker": tracker,
                })
    except Exception:
        pass
    return out


def fetch_downloader_torrents_by_domain(
    downloader_name: str,
    tracker_domains: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """按 tracker 域名筛选后返回种子列表。"""
    all_torrents = fetch_downloader_torrents(downloader_name)
    if not tracker_domains:
        return all_torrents
    domain_set = set(d.lower() for d in tracker_domains)
    from app.plugins.ptbonuscalc.MVC.utils.tracker import parse_tracker_domain
    return [
        t for t in all_torrents
        if (parse_tracker_domain(t.get("tracker")) or "").lower() in domain_set
    ]
