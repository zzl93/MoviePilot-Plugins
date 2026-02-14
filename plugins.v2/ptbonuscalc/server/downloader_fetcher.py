# -*- coding: utf-8 -*-
"""PT魔力计算器插件 - 从下载器拉取种子列表（业务：下载器对象）。"""
from typing import Any, Dict, List, Optional

from app.core.module import ModuleManager
from app.log import logger
from app.schemas.types import DownloaderType

from app.plugins.ptbonuscalc.server import utils


def get_qb_instance(downloader_name: str):
    """获取 qBittorrent 下载器实例。"""
    try:
        module_manager = ModuleManager()
        qb_modules = list(module_manager.get_running_subtype_module(DownloaderType.Qbittorrent))
        if not qb_modules:
            return None
        return qb_modules[0].get_instance(downloader_name)
    except Exception as e:
        logger.error(f"PT魔力计算器插件：获取下载器实例失败 {downloader_name}: {e}", exc_info=True)
        return None


def fetch_downloader_torrents(downloader_names: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """从配置的下载器拉取种子列表，返回 { hash: { hash, downloader, name, total_size, ... }, ... }。"""
    names = downloader_names or []
    all_torrents = {}
    for downloader_name in names:
        qb_instance = get_qb_instance(downloader_name)
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
                    "save_path": torrent.get("save_path") or "",
                    "category": torrent.get("category") or "",
                    "tags": torrent.get("tags") or "",
                    "added_on": torrent.get("added_on"),
                }
        except Exception as e:
            logger.error(f"PT魔力计算器插件：拉取下载器种子失败: {e}", exc_info=True)
    return all_torrents


def fetch_downloader_torrents_by_domain(
    downloader_names: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """拉取下载器种子并按 Tracker 域名分组返回：{ tracker_domain: { hash: dt, ... }, ... }。"""
    by_hash = fetch_downloader_torrents(downloader_names) or {}
    by_domain: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for h, dt in by_hash.items():
        domain = utils.tracker_domain_group_key(dt.get("tracker") or "")
        by_domain.setdefault(domain, {})[h] = dt
    return by_domain
