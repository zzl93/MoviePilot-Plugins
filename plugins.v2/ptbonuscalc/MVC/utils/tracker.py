"""
Tracker 解析与地址关键词转域名等工具。
"""
import re
from typing import Optional, List
from urllib.parse import urlparse


def parse_tracker_domain(tracker_url: Optional[str]) -> Optional[str]:
    """从 tracker 地址解析出域名（host），不含端口。"""
    if not tracker_url or not isinstance(tracker_url, str):
        return None
    try:
        if "://" not in tracker_url:
            tracker_url = "http://" + tracker_url.strip()
        parsed = urlparse(tracker_url)
        host = (parsed.hostname or parsed.netloc or "").strip()
        return host or None
    except Exception:
        return None


def tracker_full_host(tracker_url: Optional[str]) -> str:
    """返回完整 host（含端口），用于展示或比对。"""
    if not tracker_url or not isinstance(tracker_url, str):
        return ""
    try:
        if "://" not in tracker_url:
            tracker_url = "http://" + tracker_url.strip()
        parsed = urlparse(tracker_url)
        if parsed.port and parsed.port not in (80, 443):
            return f"{parsed.hostname or parsed.netloc}:{parsed.port}"
        return (parsed.hostname or parsed.netloc or "").strip()
    except Exception:
        return ""


def keyword_to_domain(keyword: Optional[str]) -> Optional[str]:
    """将地址关键词转为常见 tracker 域名（简化实现，可扩展映射表）。"""
    if not keyword or not keyword.strip():
        return None
    k = keyword.strip().lower()
    # 常见关键词 -> 域名片段，用于 contains 匹配
    return k


def tracker_domain_group_key(tracker_url: Optional[str]) -> str:
    """用于分组的 key：同一站点多个 tracker 归为一组。"""
    domain = parse_tracker_domain(tracker_url)
    return (domain or "").lower()
