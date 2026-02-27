# -*- coding: utf-8 -*-
"""PT魔力计算器插件 - 从下载器拉取种子列表（支持多类型：qBittorrent、Transmission 等）。"""
from typing import Any, Dict, List, Optional

from app.core.module import ModuleManager
from app.helper.service import ServiceConfigHelper
from app.log import logger
from app.schemas.types import DownloaderType

from app.plugins.ptbonuscalc.server import utils
from app.plugins.ptbonuscalc.server.downloader_normalizer import normalize_torrent_to_common

# 配置 type 字符串 -> DownloaderType
_TYPE_TO_ENUM = {
    "qbittorrent": DownloaderType.Qbittorrent,
    "transmission": DownloaderType.Transmission,
}


def get_downloader_instance(downloader_name: str):
    """
    根据下载器名称获取对应类型的下载器实例（QB/TR 等）。
    依赖主项目下载器配置中的 type 字段。
    """
    try:
        configs = ServiceConfigHelper.get_downloader_configs()
        conf = next((c for c in configs if c.name == downloader_name and c.enabled), None)
        if not conf:
            return None, None
        type_str = (conf.type or "").strip().lower()
        if not type_str:
            return None, None
        dtype = _TYPE_TO_ENUM.get(type_str)
        if not dtype:
            logger.debug(f"PT魔力计算器插件：不支持的下载器类型 type={conf.type} name={downloader_name}")
            return None, None
        module_manager = ModuleManager()
        modules = list(module_manager.get_running_subtype_module(dtype))
        if not modules:
            return None, None
        instance = modules[0].get_instance(downloader_name)
        return instance, type_str
    except Exception as e:
        logger.error(f"PT魔力计算器插件：获取下载器实例失败 {downloader_name}: {e}", exc_info=True)
        return None, None


def fetch_downloader_torrents(downloader_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    从配置的下载器拉取种子列表，返回统一格式列表，每项含 hash/downloader/name/total_size 等。
    同一 hash 若存在于多个下载器会有多条记录（按 (downloader_name, hash) 区分），便于 DB 按下载器正确写入。
    """
    names = downloader_names or []
    result: List[Dict[str, Any]] = []
    for downloader_name in names:
        instance, type_str = get_downloader_instance(downloader_name)
        if not instance or not type_str:
            continue
        try:
            torrents, error = instance.get_torrents()
            if error or not torrents:
                continue
            for raw in torrents:
                common = normalize_torrent_to_common(raw, downloader_name, type_str)
                if common and common.get("hash"):
                    result.append(common)
        except Exception as e:
            logger.error(f"PT魔力计算器插件：拉取下载器种子失败 {downloader_name}: {e}", exc_info=True)
    return result


def fetch_downloader_torrents_by_domain(
    downloader_names: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """拉取下载器种子并按 Tracker 域名分组返回：{ tracker_domain: { hash: dt, ... }, ... }。同 hash 多下载器时保留一条（按遍历顺序）。"""
    raw_list = fetch_downloader_torrents(downloader_names) or []
    by_domain: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for dt in raw_list:
        domain = utils.tracker_domain_group_key(dt.get("tracker") or "")
        by_domain.setdefault(domain, {})[dt["hash"]] = dt
    return by_domain
