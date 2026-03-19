"""
通用工具：配置解析、展示宽度、名称匹配等。
"""
import re
from typing import List, Any, Optional


def parse_list_config(value: Any) -> List[str]:
    """将配置项解析为字符串列表（逗号/列表等）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).split(",") if x.strip()]


def torrent_key(site_id: Optional[int], torrent_id: Optional[str]) -> str:
    """生成站点种子唯一 key。"""
    return f"{site_id or 0}_{torrent_id or ''}"


def display_width(s: str) -> int:
    """计算字符串显示宽度（中文约 2，英文约 1）。"""
    if not s:
        return 0
    w = 0
    for c in str(s):
        w += 2 if "\u4e00" <= c <= "\u9fff" else 1
    return w


def truncate_by_display_width(s: str, max_width: int, suffix: str = "...") -> str:
    """按显示宽度截断字符串。"""
    if not s or max_width <= 0:
        return ""
    if display_width(s) <= max_width:
        return s
    w = 0
    for i, c in enumerate(str(s)):
        w += 2 if "\u4e00" <= c <= "\u9fff" else 1
        if w >= max_width - display_width(suffix):
            return s[: i + 1] + suffix
    return s + suffix


def name_match_at_least_n_tokens(name_a: Optional[str], name_b: Optional[str], n: int = 2) -> bool:
    """判断两个名称是否至少有 n 个 token 匹配（按空格/常见分隔符分词）。"""
    if not name_a or not name_b or n <= 0:
        return False
    sep = re.compile(r"[\s.\-_\[\]]+")
    tokens_a = set(sep.split(name_a.lower()))
    tokens_b = set(sep.split(name_b.lower()))
    return len(tokens_a & tokens_b) >= n
