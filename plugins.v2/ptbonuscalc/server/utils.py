# -*- coding: utf-8 -*-
"""插件公共方法：Tracker 域名解析与分组、发布时间/魔力公式、配置解析、种子键与显示宽度、名称分词与匹配等。供 seedinfo_oper、插件 __init__ 等复用。"""
import math
import re
import unicodedata
from datetime import datetime
from typing import Tuple, Dict, List, Any, Optional, Set
from urllib.parse import urlparse

from app.utils.string import StringUtils

try:
    import jieba
except ImportError:
    jieba = None


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


def tracker_full_host(tracker: str) -> str:
    """获取 tracker 完整主机名（小写），用于地址映射匹配。空串返回 ""。"""
    _, host = parse_tracker_domain(tracker)
    return (host or "").lower()


def keyword_to_domain(kw: Optional[str]) -> str:
    """将配置中的关键词（URL 或域名片段）转为统一域名（小写）。用于站点地址映射与候选筛选。"""
    if not kw:
        return ""
    k = str(kw).strip()
    if "://" in k or ("." in k and "/" in k):
        h = tracker_full_host(k)
        return h or StringUtils.get_url_domain(k) or k
    return k.lower()


def tracker_domain_group_key(tracker: str) -> str:
    """从 tracker 得到分组键：有 host 返回小写 host，否则返回 __no_tracker__。用于按 tracker 域名分组。"""
    if not (tracker or "").strip():
        return "__no_tracker__"
    return tracker_full_host(tracker) or "__no_tracker__"


def build_torrents_by_tracker_domain(
    downloader_torrents: Dict[str, Dict[str, Any]],
) -> Dict[str, List[str]]:
    """将下载器种子按 tracker 域名分组，返回 { 域名: [hash, ...], ... }。"""
    by_domain: Dict[str, List[str]] = {}
    for hash_value, t in downloader_torrents.items():
        gk = tracker_domain_group_key((t.get("tracker") or "").strip())
        by_domain.setdefault(gk, []).append(hash_value)
    return by_domain


# ---------- 发布时间、魔力公式、配置解析、种子键、显示宽度 ----------


def parse_pubdate_weeks(pubdate: Optional[str]) -> Optional[float]:
    """解析发布时间字符串，返回距今周数；解析失败返回 None。"""
    if not pubdate or not isinstance(pubdate, str):
        return None
    s = pubdate.strip().replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ").replace("/", "-").replace("T", " ").rstrip("Z").strip()
    fmts = [
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ]
    for fmt, length in fmts:
        try:
            dt = datetime.strptime(s[:length], fmt)
            delta = datetime.now() - dt
            return max(0.0, delta.total_seconds() / (86400 * 7))
        except ValueError:
            continue
    return None


def calc_bonus_per_hour(T_weeks: float, S_GB: float, N: int, T0: int, N0: int, B0: int, L: int, weight: float = 1.0) -> tuple:
    """NexusPHP 魔力公式。返回 (B 每小时魔力, A 值, A/GB)。"""
    if T0 <= 0 or N0 <= 0 or B0 <= 0 or L <= 0:
        return 0.0, 0.0, 0.0
    c1 = 1.0 - math.pow(10, -T_weeks / T0)
    n = max(1, N)
    c2 = 1.0 + math.sqrt(2) * math.pow(10, -(n - 1) / (N0 - 1))
    A = c1 * S_GB * c2 * weight
    B = B0 * (2.0 / math.pi) * math.atan(A / L)
    ave = round(A / S_GB, 2) if S_GB > 0 else 0.0
    return round(B, 4), round(A, 2), ave


def parse_list_config(value: Any) -> List[str]:
    """配置项解析：支持 list 或逗号分隔字符串。"""
    if isinstance(value, list):
        return [str(x).strip() for x in value if x]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def torrent_key(torrent_id: Any, name: str = "", size: Any = 0) -> str:
    """从 torrent_id / name / size 得到唯一键。"""
    return str(torrent_id) if torrent_id else f"{name or ''}|{size or 0}"


def display_width(s: str) -> int:
    """计算字符串显示宽度，中文/日文=2，英文=1。"""
    if not s:
        return 0
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def truncate_by_display_width(s: str, max_width: int, suffix: str = "...") -> tuple:
    """按显示宽度截断字符串。返回 (截断后字符串, 是否被截断)。"""
    if not s or max_width <= 0:
        return (s or "", False)
    width = 0
    for i, c in enumerate(s):
        w = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if width + w > max_width:
            return (s[:i] + suffix, True)
        width += w
    return (s, False)


# ---------- 名称分词、匹配、做种时长格式化 ----------


def tokenize_name_for_match(name: str) -> Set[str]:
    """对种子名称做多语言分词，用于匹配：中文用分词，英文/数字按词切分。返回 token 集合（小写）。"""
    if not name or not isinstance(name, str):
        return set()
    s = re.sub(r"[.\-_\[\](){}\s]+", " ", name).strip()
    if not s:
        return set()
    tokens = set()
    pattern = re.compile(r"([\u4e00-\u9fff]+)|([a-zA-Z0-9]+)")
    for m in pattern.finditer(s):
        cn, en = m.group(1), m.group(2)
        if cn:
            if jieba:
                for w in jieba.cut(cn, HMM=False):
                    w = (w or "").strip()
                    if len(w) >= 1:
                        tokens.add(w)
            else:
                for c in cn:
                    if c.strip():
                        tokens.add(c)
        if en:
            tokens.add(en.lower())
    return tokens


def token_overlap_score(tokens_site: Set[str], tokens_dl: Set[str]) -> float:
    """名称 token 重叠度：Jaccard = |交|/|并|，无 token 时返回 0。"""
    if not tokens_site or not tokens_dl:
        return 0.0
    inter = len(tokens_site & tokens_dl)
    union = len(tokens_site | tokens_dl)
    return inter / union if union else 0.0


def match_torrent_core(
    site_torrent: Dict[str, Any],
    site_domain: str,
    downloader_torrents: Dict[str, Dict[str, Any]],
    mappings: Dict[str, str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    站点种子与下载器种子匹配核心逻辑：先查 mappings；再在 downloader_torrents 内按名称分词重叠度与总大小筛选。
    调用方需传入已按 Tracker 筛选的该站点候选与当前 mappings。
    """
    torrent_id = site_torrent.get("torrent_id")
    name = site_torrent.get("name") or ""
    size = float(site_torrent.get("size") or 0)
    if torrent_id and f"{site_domain}|{torrent_id}" in mappings:
        return (mappings[f"{site_domain}|{torrent_id}"], "手动映射")
    if f"{site_domain}|{name}|{size}" in mappings:
        return (mappings[f"{site_domain}|{name}|{size}"], "手动映射")
    if not name or size <= 0 or not downloader_torrents:
        return (None, None)
    site_tokens = tokenize_name_for_match(name)
    size_min, size_max = size * 0.99, size * 1.01
    token_min_score = 0.25
    candidates_with_score = []
    for hash_value, t in downloader_torrents.items():
        dl_size = t.get("total_size") or 0
        if not (size_min <= dl_size <= size_max):
            continue
        dl_tokens = tokenize_name_for_match(t.get("name") or "")
        score = token_overlap_score(site_tokens, dl_tokens)
        if score >= token_min_score:
            candidates_with_score.append((score, hash_value, t))
    candidates_with_score.sort(reverse=True, key=lambda x: x[0])
    if candidates_with_score:
        return (candidates_with_score[0][1], "分词+大小匹配")
    return (None, None)


def format_seeding_time(seconds: int) -> str:
    """将做种秒数格式化为可读字符串。"""
    if seconds <= 0:
        return "0秒"
    days, hours, minutes = seconds // 86400, (seconds % 86400) // 3600, (seconds % 3600) // 60
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def get_candidate_torrents_for_site(
    site_domain: str,
    downloader_torrents: Dict[str, Dict[str, Any]],
    torrents_by_domain: Dict[str, List[str]],
    site_address_mappings: Dict[str, List[str]],
) -> Dict[str, Dict[str, Any]]:
    """按站点地址映射筛选该站点可选的下载器种子。"""
    keywords = site_address_mappings.get(site_domain, [])
    if not keywords:
        return downloader_torrents
    keyword_domains = set()
    for kw in keywords:
        k = (kw or "").strip()
        if not k:
            continue
        d = keyword_to_domain(k)
        if d:
            keyword_domains.add(d)
    if not keyword_domains:
        return downloader_torrents
    candidate_hashes = []
    for gk, hashes in torrents_by_domain.items():
        if gk in keyword_domains:
            candidate_hashes.extend(hashes)
    if not candidate_hashes:
        return downloader_torrents
    return {h: downloader_torrents[h] for h in candidate_hashes if h in downloader_torrents}
