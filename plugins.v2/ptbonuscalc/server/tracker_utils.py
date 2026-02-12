# -*- coding: utf-8 -*-
"""Tracker 域名解析：原始 URL 保留，提取二级/一级域名"""
from typing import Tuple
from urllib.parse import urlparse


def parse_tracker_domain(tracker_url: str) -> Tuple[str, str]:
    """
    从 tracker URL 解析域名。
    :param tracker_url: 原始 tracker 地址
    :return: (原始 tracker, 解析后的域名)
    解析规则：有子域名（如 pt.example.com）则保留完整 host；否则保留一级域名（如 example.com）
    """
    if not (tracker_url or "").strip():
        return "", ""
    orig = (tracker_url or "").strip()
    try:
        parsed = urlparse(orig if "://" in orig else f"http://{orig}")
        netloc = parsed.netloc or parsed.path.split("/")[0]
        host = netloc.split(":")[0]
        if not host:
            return orig, ""
        parts = [p for p in host.split(".") if p]
        if len(parts) >= 3:
            return orig, host  # 有子域名，保留完整如 pt.example.com
        if len(parts) == 2:
            return orig, host  # 一级域名，保留如 example.com
        return orig, host
    except Exception:
        return orig, ""
