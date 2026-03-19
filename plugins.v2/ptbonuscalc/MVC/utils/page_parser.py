"""
NexusPHP 页面解析：提供站点做种表与 mybonus 参数的解析能力。
支持按 site_config (JSON) 配置驱动解析，不同站点可通过 site_configs/*.json 定制。
"""
import re
from typing import Optional, Dict, Any, List

from lxml import etree

from app.utils.string import StringUtils
from app.log import logger

_SIZE_KEYWORDS = ["size", "大小"]
_SEEDERS_KEYWORDS = ["seeders", "种子", "在做种", "做种"]
_LEECHERS_KEYWORDS = ["leechers", "下载"]
_PUBLISH_TIME_KEYWORDS = ["发布时间", "发表", "published", "time"]
_SEED_TIME_KEYWORDS = ["做种时间", "seeding time"]
_WEIGHT_KEYWORDS = ["权重", "weight"]
_CLIENT_KEYWORDS = ["客户端", "client"]


def _get_column_keywords(config: Dict, field: str) -> List[str]:
    """从配置取列关键词，无则用内置默认。"""
    kw = config.get("column_keywords") or {}
    if isinstance(kw.get(field), list):
        return kw[field]
    defaults = {
        "size": _SIZE_KEYWORDS,
        "seeders": _SEEDERS_KEYWORDS,
        "leechers": _LEECHERS_KEYWORDS,
        "publish_time": _PUBLISH_TIME_KEYWORDS,
        "seed_time": _SEED_TIME_KEYWORDS,
        "weight": _WEIGHT_KEYWORDS,
        "client": _CLIENT_KEYWORDS,
    }
    return defaults.get(field, [])


def _get_table_element_by_config(html, config: Dict) -> Optional[etree._Element]:
    """按配置的 table_selectors 查找做种表，找不到则用内置 _get_table_element。"""
    selectors = config.get("table_selectors")
    if isinstance(selectors, list):
        for sel in selectors:
            if not sel or not isinstance(sel, str):
                continue
            try:
                tables = html.xpath(sel)
                if tables:
                    return tables[0]
            except Exception:
                continue
    return _get_table_element(html)

_DURATION_UNITS = [
    (("周", "週", "weeks"), 7 * 24 * 3600),
    (("天", "日", "days"), 24 * 3600),
    (("小时", "小時", "时", "時", "hours", "h"), 3600),
    (("分钟", "分", "分鐘", "min", "m"), 60),
    (("秒", "s"), 1),
]


def _prepare_html_text(html_text: str) -> str:
    """去掉无法解析的样式噪声，确保 etree 能正确解析。"""
    return re.sub(r"#\d+", "", re.sub(r"\d+px", "", html_text or ""))


def _get_table_element(html) -> Optional[etree._Element]:
    """返回包含做种表格的 table 元素。"""
    tables = html.xpath('//table[.//img[@class="size" or @alt="size" or @title="size"]]')
    if tables:
        return tables[0]
    tables = html.xpath('//table[.//td[contains(translate(normalize-space(string(.)), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "seeders")]]')
    if tables:
        return tables[0]
    return html.xpath("//table")[0] if html.xpath("//table") else None


def _collect_header_cells(table: etree._Element) -> List[etree._Element]:
    headers = table.xpath(".//thead/tr[1]/*[self::td or self::th]")
    if headers:
        return headers
    return table.xpath(".//tr[1]/*[self::td or self::th]")


def _header_text(cell: etree._Element) -> str:
    text = " ".join(cell.xpath(".//text()")) or ""
    img_text = " ".join(cell.xpath('.//img/@alt | .//img/@title'))
    return f"{text} {img_text}".strip()


def _find_column_index(headers: List[etree._Element], keywords: List[str]) -> Optional[int]:
    lowered_keywords = [k.lower() for k in keywords if k]
    for idx, cell in enumerate(headers, start=1):
        text = _header_text(cell).replace("\n", " ").strip()
        lowered = text.lower()
        if any(kw and kw in lowered for kw in lowered_keywords):
            return idx
        if any(kw and kw in text for kw in keywords):
            return idx
    return None


def _parse_duration_to_seconds(text: Optional[str]) -> int:
    """解析“3天12小时34分”/“2d 04:20:00”/“120:30:00”一类的时间表示。"""
    if not text:
        return 0
    raw = text.replace(",", " ").replace("＋", "+").replace("+", " ").strip()
    if not raw:
        return 0
    colon_match = re.search(r"(?:(\d+)\s*天)?\s*(\d{1,3}):(\d{1,2}):(\d{1,2})", raw)
    if colon_match:
        days = int(colon_match.group(1) or 0)
        hours = int(colon_match.group(2))
        minutes = int(colon_match.group(3))
        seconds = int(colon_match.group(4))
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    total = 0
    for unit_group, multiplier in _DURATION_UNITS:
        pattern = r"(\d+(?:\.\d+)?)\s*(%s)" % "|".join(unit_group)
        for value, _ in re.findall(pattern, raw, flags=re.IGNORECASE):
            try:
                total += float(value) * multiplier
            except ValueError:
                continue
    if total > 0:
        return int(total)
    if raw.isdigit():
        return int(raw) * 3600
    return 0


def parse_bonus_params_nexusphp(html_text: str) -> Dict[str, Any]:
    """解析 NexusPHP mybonus 页面中的 T0/N0/B0/L 等参数。"""
    html_text = _prepare_html_text(html_text or "")
    if not html_text.strip():
        return {}
    html = etree.HTML(html_text)
    if html is None or not StringUtils.is_valid_html_element(html):
        return {}
    params: Dict[str, Any] = {}
    for key in ("T0", "N0", "B0", "L"):
        lis = html.xpath(f'//li[.//b[contains(text(), "{key}")]]')
        if not lis:
            continue
        li = lis[1] if len(lis) > 1 else lis[0]
        text = li.xpath("string(.)").strip()
        parts = text.split("=", 1)
        if len(parts) < 2:
            continue
        try:
            params[key] = int(parts[1].strip().split()[0].replace(",", ""))
        except (ValueError, IndexError):
            continue
    seed_block = html.xpath("//li[contains(., '做种') and contains(., '奖励')]/text()")
    block_text = "".join(seed_block) if seed_block else html_text
    m_seed = re.search(r"(\d+(?:\.\d+)?)\s*个?奖励", block_text)
    if m_seed:
        try:
            params["seeding_bonus_per_seed"] = float(m_seed.group(1))
        except ValueError:
            pass
    m_cap = re.search(r"最多\s*(\d+)\s*个", block_text)
    if m_cap:
        try:
            params["seeding_bonus_cap"] = int(m_cap.group(1))
        except ValueError:
            pass
    m_wi = re.search(r"默认为\s*([\d.]+).*?权重.*?为\s*([\d.]+)", html_text, re.DOTALL)
    if m_wi:
        try:
            params["default_weight"] = float(m_wi.group(1))
            params["zero_bonus_weight"] = float(m_wi.group(2))
        except ValueError:
            pass
    m_hour = re.search(r"每小时可获得\s*([\d.]+)", html_text)
    if m_hour:
        val = m_hour.group(1).strip()
        decimals = len(val.split(".", 1)[1]) if "." in val else 0
        params["hourly_bonus_decimals"] = decimals
    return params


def _extract_torrent_id_from_row(row: etree._Element, config: Dict) -> tuple:
    """从行中按配置的 torrent_id_sources 提取 torrent_id 与 detail_path。返回 (torrent_id, detail_path)。"""
    torrent_id = ""
    detail_path = ""
    sources = config.get("torrent_id_sources") or []
    if not isinstance(sources, list):
        sources = []
    for src in sources:
        xpath_expr = src.get("xpath") if isinstance(src, dict) else None
        regex_str = src.get("regex") if isinstance(src, dict) else None
        if not xpath_expr:
            continue
        try:
            found = row.xpath(xpath_expr)
            for val in (found if isinstance(found, list) else [found]):
                if val is None:
                    continue
                text = str(val).strip()
                if not text:
                    continue
                if "details.php" in text or "torrents.php" in text:
                    detail_path = text
                if regex_str:
                    m = re.search(regex_str, text)
                    if m:
                        torrent_id = m.group(1)
                        return (torrent_id, detail_path or text)
        except Exception:
            continue
    return (torrent_id, detail_path)


def _extract_name_from_row(row: etree._Element, config: Dict, cells: List) -> str:
    """按配置的 name_xpath 提取种子名，无则取首列文本。"""
    name_xpath = config.get("name_xpath")
    if name_xpath and isinstance(name_xpath, str):
        try:
            els = row.xpath(name_xpath)
            if els:
                return (els[0].xpath("string(.)") if hasattr(els[0], "xpath") else str(els[0])).strip()
        except Exception:
            pass
    if cells:
        return cells[0].xpath("string(.)").strip()
    return ""


def parse_torrent_activity_with_config(html_text: str, config: Dict) -> Dict[str, Any]:
    """按配置解析做种表，返回 { torrents: [...], bonus_params: {} }。"""
    html_text = _prepare_html_text(html_text or "")
    html = etree.HTML(html_text.replace(r"\/", "/"))
    result = {"torrents": [], "bonus_params": {}}
    if html is None or not StringUtils.is_valid_html_element(html):
        logger.warning("[ptbonuscalc] parse_torrent_activity_with_config html 无效")
        return result
    all_tables = html.xpath("//table")
    logger.info(f"[ptbonuscalc] parse_torrent_activity 共 {len(all_tables)} 个 table")
    table = _get_table_element_by_config(html, config or {})
    if table is None:
        logger.warning("[ptbonuscalc] parse_torrent_activity 未找到做种表")
        return result
    headers = _collect_header_cells(table)
    kw = config or {}
    size_col = _find_column_index(headers, _get_column_keywords(kw, "size")) or 3
    seeders_col = _find_column_index(headers, _get_column_keywords(kw, "seeders")) or 4
    leechers_col = _find_column_index(headers, _get_column_keywords(kw, "leechers"))
    publish_col = _find_column_index(headers, _get_column_keywords(kw, "publish_time"))
    seed_time_col = _find_column_index(headers, _get_column_keywords(kw, "seed_time"))
    weight_col = _find_column_index(headers, _get_column_keywords(kw, "weight"))
    client_col = _find_column_index(headers, _get_column_keywords(kw, "client"))
    rows = table.xpath(".//tr[position()>1]")
    all_tr = table.xpath(".//tr")
    logger.info(
        f"[ptbonuscalc] parse_torrent_activity 表头列 size={size_col} seeders={seeders_col} seed_time={seed_time_col} "
        f"总tr={len(all_tr)} 数据行(除表头)={len(rows)}"
    )
    for row in rows:
        cells = row.xpath("./td")
        if not cells:
            continue
        size_text = cells[size_col - 1].xpath("string(.)").strip() if size_col and size_col <= len(cells) else ""
        size_bytes = StringUtils.num_filesize(size_text)
        seeders_text = cells[seeders_col - 1].xpath("string(.)").strip() if seeders_col and seeders_col <= len(cells) else ""
        seeders = StringUtils.str_int(seeders_text)
        leechers = 0
        if leechers_col and leechers_col <= len(cells):
            leechers_text = cells[leechers_col - 1].xpath("string(.)").strip()
            leechers = StringUtils.str_int(leechers_text)
        pubdate = ""
        if publish_col and publish_col <= len(cells):
            pubdate_raw = cells[publish_col - 1].xpath("string(.)").strip()
            pubdate = pubdate_raw.replace("\xa0", " ")
        seed_time_seconds = 0
        seed_time_text = ""
        if seed_time_col and seed_time_col <= len(cells):
            seed_time_text = cells[seed_time_col - 1].xpath("string(.)").strip()
            seed_time_seconds = _parse_duration_to_seconds(seed_time_text)
        weight = None
        if weight_col and weight_col <= len(cells):
            weight_text = cells[weight_col - 1].xpath("string(.)").strip()
            try:
                weight = float(weight_text)
            except ValueError:
                if "0.2" in weight_text:
                    weight = 0.2
        client = ""
        if client_col and client_col <= len(cells):
            client = cells[client_col - 1].xpath("string(.)").strip()
        torrent_id, detail_path = _extract_torrent_id_from_row(row, kw)
        name = _extract_name_from_row(row, kw, cells)
        if not torrent_id and len(result["torrents"]) < 3:
            row_links = row.xpath(".//a/@href")
            logger.info(f"[ptbonuscalc] parse_torrent_activity 前几行链接样例(无有效torrent_id): {row_links[:8]}")
        torrent_entry = {
            "torrent_id": torrent_id,
            "id": torrent_id or name,
            "name": name,
            "size": size_bytes,
            "seed_time": seed_time_seconds,
            "bonus_per_hour": 0.0,
            "extra": {
                "seeders": seeders,
                "leechers": leechers,
                "pubdate": pubdate,
                "seed_time_text": seed_time_text,
                "weight": weight,
                "client": client,
                "detail_path": detail_path,
            },
        }
        result["torrents"].append(torrent_entry)
    with_tid = sum(1 for t in result["torrents"] if t.get("torrent_id"))
    if result["torrents"] and with_tid == 0:
        logger.warning(
            f"[ptbonuscalc] parse_torrent_activity 解析到 {len(result['torrents'])} 行但无有效 torrent_id"
        )
    return result


def parse_torrent_activity_nexusphp(html_text: str, site_config: Optional[Dict] = None) -> Dict[str, Any]:
    """解析 NexusPHP 做种表。若有 site_config 则按配置解析，否则用 default 配置。"""
    if site_config is not None:
        return parse_torrent_activity_with_config(html_text, site_config or {})
    from app.plugins.ptbonuscalc.MVC.utils.site_config_loader import get_site_parser_config
    default_cfg = get_site_parser_config("")
    return parse_torrent_activity_with_config(html_text, default_cfg)


def extract_userid_from_index_nexusphp(html_text: str) -> Optional[str]:
    """从 index 页面解析 userid（userdetails.php?id=xxx 或 getusertorrentlistajax 调用）。"""
    html_text = _prepare_html_text(html_text or "")
    m = re.search(r"userdetails\.php\?id=(\d+)", html_text)
    if m:
        return m.group(1).strip()
    m = re.search(r"getusertorrentlistajax\s*\(\s*['\"]?(\d+)", html_text)
    if m:
        return m.group(1).strip()
    return None
