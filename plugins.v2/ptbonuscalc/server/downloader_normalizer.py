# -*- coding: utf-8 -*-
"""将各下载器原始种子数据统一为插件通用格式（与 ptbonuscalc_downloader_seed 主表/快照表字段对齐）。"""
from datetime import datetime
from typing import Any, Dict, Optional, Union


def _safe_get(obj: Any, key: str, default: Any = None):
    """从 dict 或 object 取值。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _tr_tracker(torrent: Any) -> str:
    """从 Transmission Torrent 取首个 tracker URL。"""
    trackers = _safe_get(torrent, "trackers") or _safe_get(torrent, "tracker_list") or []
    if not trackers:
        return ""
    first = trackers[0] if trackers else None
    if first is None:
        return ""
    if isinstance(first, str):
        return (first or "").strip()
    return (getattr(first, "announce", None) or getattr(first, "url", None) or "").strip()


def _tr_tags(torrent: Any) -> str:
    """Transmission 的 labels 转为逗号分隔字符串。"""
    labels = _safe_get(torrent, "labels") or []
    if not labels:
        return ""
    return ",".join(str(x).strip() for x in labels if x)


def _tr_added_at_timestamp(torrent: Any) -> Optional[Union[int, float]]:
    """Transmission 的 added_date 转为 Unix 时间戳，与 QB 的 added_on 一致，供 DB 写入。"""
    added = _safe_get(torrent, "added_date") or _safe_get(torrent, "addedDate")
    if added is None:
        return None
    if isinstance(added, (int, float)):
        return added
    if isinstance(added, datetime):
        try:
            return added.timestamp()
        except (ValueError, OSError):
            return None
    return None


def normalize_torrent_to_common(
    raw: Union[Dict[str, Any], Any],
    downloader_name: str,
    source_type: str,
) -> Optional[Dict[str, Any]]:
    """
    将 qBittorrent / Transmission 的单条种子数据统一为插件通用格式。
    与 downloader_seed 主表及快照表字段对应，供 fetch_downloader_torrents 使用。

    :param raw: 原始数据，QB 为 dict，TR 为 transmission_rpc.Torrent 等
    :param downloader_name: 下载器名称
    :param source_type: 下载器类型，如 "qbittorrent" 或 "transmission"（不区分大小写）
    :return: 统一格式 dict，含 hash/downloader/name/total_size/ratio/uploaded/downloaded/
             seeding_time/state/tracker/save_path/category/tags/added_on；无法取到 hash 时返回 None
    """
    st = (source_type or "").strip().lower()
    if st == "qbittorrent":
        return _normalize_qb(raw, downloader_name)
    if st == "transmission":
        return _normalize_tr(raw, downloader_name)
    return None


def _normalize_qb(torrent: Dict[str, Any], downloader_name: str) -> Optional[Dict[str, Any]]:
    """qBittorrent 原始 dict 转为通用格式。"""
    hash_value = torrent.get("hash") if isinstance(torrent, dict) else None
    if not hash_value:
        return None
    tracker = torrent.get("tracker") or ""
    if not tracker and isinstance(torrent.get("trackers"), list) and torrent["trackers"]:
        first = torrent["trackers"][0]
        tracker = (first.get("url") if isinstance(first, dict) else str(first)) or ""
    return {
        "hash": hash_value,
        "downloader": downloader_name,
        "name": torrent.get("name") or "",
        "total_size": int(torrent.get("total_size") or 0),
        "ratio": float(torrent.get("ratio") or 0.0),
        "uploaded": int(torrent.get("uploaded") or 0),
        "downloaded": int(torrent.get("downloaded") or 0),
        "seeding_time": int(torrent.get("seeding_time") or 0),
        "state": str(torrent.get("state") or ""),
        "tracker": tracker,
        "save_path": str(torrent.get("save_path") or ""),
        "category": str(torrent.get("category") or ""),
        "tags": str(torrent.get("tags") or ""),
        "added_on": torrent.get("added_on"),
    }


def _normalize_tr(torrent: Any, downloader_name: str) -> Optional[Dict[str, Any]]:
    """Transmission Torrent 对象转为通用格式（与 QB 字段一致）。"""
    hash_value = _safe_get(torrent, "hash_string") or _safe_get(torrent, "hashString") or _safe_get(torrent, "hash")
    if not hash_value:
        return None
    # total_size: transmission_rpc 有 total_size 属性
    total_size = _safe_get(torrent, "total_size") or _safe_get(torrent, "totalSize") or 0
    ratio = _safe_get(torrent, "ratio") or _safe_get(torrent, "upload_ratio") or _safe_get(torrent, "uploadRatio") or 0.0
    uploaded = _safe_get(torrent, "uploaded_ever") or _safe_get(torrent, "uploadedEver") or 0
    downloaded = _safe_get(torrent, "downloaded_ever") or _safe_get(torrent, "downloadedEver") or 0
    save_path = _safe_get(torrent, "download_dir") or _safe_get(torrent, "downloadDir") or ""
    status = _safe_get(torrent, "status")
    if status is not None and hasattr(status, "value"):
        state = str(status.value)
    else:
        state = str(status) if status is not None else ""
    added_on = _tr_added_at_timestamp(torrent)
    return {
        "hash": str(hash_value),
        "downloader": downloader_name,
        "name": str(_safe_get(torrent, "name") or ""),
        "total_size": int(total_size),
        "ratio": float(ratio),
        "uploaded": int(uploaded),
        "downloaded": int(downloaded),
        "seeding_time": 0,  # TR 无直接做种时长字段，暂填 0
        "state": state,
        "tracker": _tr_tracker(torrent),
        "save_path": str(save_path),
        "category": "",
        "tags": _tr_tags(torrent),
        "added_on": added_on,
    }
