"""
魔力相关解析与计算。
"""
import math
from datetime import datetime
import re
from typing import Optional, Dict, Any

SECONDS_PER_WEEK = 7 * 24 * 3600


def parse_pubdate_weeks(pub_date_str: Optional[str]) -> Optional[float]:
    """将发布时间字符串换算成“距离现在的周数”（用于兼容旧数据）。"""
    if not pub_date_str:
        return None
    text = pub_date_str.strip().replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ").replace("/", "-")
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(text[: len(fmt)], fmt)
            delta = datetime.now() - dt
            return max(0.0, delta.total_seconds() / SECONDS_PER_WEEK)
        except ValueError:
            continue
    match_weeks = re.search(r"(\d+(?:\.\d+)?)\s*周", text)
    if match_weeks:
        try:
            return float(match_weeks.group(1))
        except ValueError:
            return None
    return None


def calc_bonus_per_hour(
    size_bytes: int,
    seed_time_seconds: int,
    bonus_params: Optional[Dict[str, Any]] = None,
    *,
    seeders: Optional[int] = None,
    weight: Optional[float] = None,
) -> float:
    """
    根据 NexusPHP 公示计算做种奖励（单位：每小时）。
    公式参考旧版 ptbonuscalc/server/utils.py。
    """
    if not bonus_params:
        return 0.0
    try:
        T0 = int(bonus_params.get("T0") or 0)
        N0 = int(bonus_params.get("N0") or 0)
        B0 = int(bonus_params.get("B0") or 0)
        L = int(bonus_params.get("L") or 0)
    except (TypeError, ValueError):
        return 0.0
    if min(T0, N0, B0, L) <= 0:
        return 0.0
    size_gb = float(size_bytes or 0) / (1024 ** 3)
    if size_gb <= 0:
        return 0.0
    seed_time_seconds = max(0, int(seed_time_seconds or 0))
    T_weeks = seed_time_seconds / SECONDS_PER_WEEK
    c1 = 1.0 - math.pow(10, -T_weeks / T0) if T0 > 0 else 0.0
    n = max(1, int(seeders) if seeders is not None else 1)
    denom = max(1, N0 - 1)
    c2 = 1.0 + math.sqrt(2) * math.pow(10, -(n - 1) / denom)
    w = weight if weight is not None else float(bonus_params.get("default_weight") or 1.0)
    A = c1 * size_gb * c2 * w
    if A <= 0:
        return 0.0
    bonus = B0 * (2.0 / math.pi) * math.atan(A / L)
    decimals = int(bonus_params.get("hourly_bonus_decimals") or 2)
    return round(bonus, max(0, decimals))

