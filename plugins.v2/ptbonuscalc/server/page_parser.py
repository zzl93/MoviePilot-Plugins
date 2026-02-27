# -*- coding: utf-8 -*-
"""
NexusPHP 页面解析：torrent_activity（做种列表）与 bonus_params（魔力参数）。
解析结果：torrent_activity 存入 ptbonuscalc_seedinfo，bonus_params 存入 PluginData。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from app.log import logger
from app.utils.string import StringUtils


def _prepare_html_text(html_text: str) -> str:
    """处理 HTML 干扰字符"""
    if not html_text:
        return ""
    return re.sub(r"#\d+", "", re.sub(r"\d+px", "", html_text))


def parse_bonus_params_nexusphp(html_text: str, site_name: str = "") -> Dict[str, Any]:
    """
    解析 NexusPHP mybonus 页的魔力参数（T0, N0, B0, L 等）。
    :param html_text: mybonus 页面 HTML
    :param site_name: 站点名，用于日志
    :return: bonus_params 字典，失败返回空 dict
    """
    if not html_text or not html_text.strip():
        return {}
    html = etree.HTML(_prepare_html_text(html_text))
    try:
        if not StringUtils.is_valid_html_element(html):
            return {}
        params = {}
        for key in ("T0", "N0", "B0", "L"):
            lis = html.xpath(f'//li[.//b[contains(text(), "{key}")]]')
            if not lis:
                continue
            li = lis[1] if len(lis) > 1 else lis[0]
            text = li.xpath("string(.)").strip()
            parts = text.split(" = ", 1)
            if len(parts) < 2:
                continue
            try:
                params[key] = int(parts[1].strip().split()[0].replace(",", ""))
            except (ValueError, IndexError):
                continue
        seed_block = ""
        try:
            lis_seed = html.xpath("//li[contains(., '做种数') and contains(., '个魔力值')]")
            if lis_seed:
                seed_block = lis_seed[0].xpath("string(.)") or ""
        except Exception:
            seed_block = html_text
        if not seed_block:
            seed_block = html_text
        m_seed = re.search(r"(\d+(?:\.\d+)?)\s*个魔力值\s*\*", seed_block)
        m_cap = re.search(r"最多计\s*(\d+)\s*个", seed_block)
        if m_seed:
            try:
                params["seeding_bonus_per_seed"] = float(m_seed.group(1))
            except (ValueError, IndexError):
                pass
        if m_cap:
            try:
                params["seeding_bonus_cap"] = int(m_cap.group(1))
            except (ValueError, IndexError):
                pass
        m_wi = re.search(r"默认为\s*(\d+(?:\.\d+)?).*?零魔.*?为\s*(\d+(?:\.\d+)?)", html_text, re.DOTALL)
        if m_wi:
            try:
                params["default_weight"] = float(m_wi.group(1))
                params["zero_bonus_weight"] = float(m_wi.group(2))
            except (ValueError, IndexError):
                pass
        m_hour = re.search(r"每小时能获取\s*([\d.]+)\s*个", html_text)
        if m_hour:
            s_val = m_hour.group(1).strip()
            if "." in s_val:
                params["hourly_bonus_decimals"] = len(s_val.split(".", 1)[1])
            else:
                params["hourly_bonus_decimals"] = 0
        if params and site_name:
            logger.debug(f"{site_name} 魔力参数解析: {params}")
    finally:
        if html is not None:
            del html
    return params


def parse_torrent_activity_nexusphp(
    html_text: str,
    site_name: str = "",
    multi_page: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    解析 NexusPHP 做种页面，返回做种列表及下页地址。
    :param html_text: 做种页面 HTML
    :param site_name: 站点名，用于日志
    :param multi_page: 是否多页模式
    :return: (seeding_detail 列表, 下页 URL 或 None)
    """
    if not html_text:
        return [], None
    html = etree.HTML(str(html_text).replace(r"\/", "/"))
    try:
        if not StringUtils.is_valid_html_element(html):
            return [], None
        seeding_url_text = html.xpath(
            '//a[contains(@href,"torrents.php") and contains(@href,"seeding")]/@href'
        )
        if not multi_page and seeding_url_text and seeding_url_text[0].strip():
            return [], seeding_url_text[0].strip()
        size_col = 3
        seeders_col = 4
        time_col = None
        size_col_xpath = (
            '//tr[position()=1]/'
            'td[(img[@class="size"] and img[@alt="size"]) or (text() = "大小") or (a/img[@class="size" and @alt="size"])]'
        )
        if html.xpath(size_col_xpath):
            size_col = len(html.xpath(f"{size_col_xpath}/preceding-sibling::td")) + 1
        seeders_col_xpath = (
            '//tr[position()=1]/'
            'td[(img[@class="seeders"] and img[@alt="seeders"]) or (text() = "在做种") or (a/img[@class="seeders" and @alt="seeders"])]'
        )
        if html.xpath(seeders_col_xpath):
            seeders_col = len(html.xpath(f"{seeders_col_xpath}/preceding-sibling::td")) + 1
        time_col_xpath = '//tr[position()=1]/td[(img[@class="time"] and img[@alt="time"]) or (text()="发生时间") or (text()="发布时间") or (normalize-space(.)="发布时间")]'
        if html.xpath(time_col_xpath):
            time_col = len(html.xpath(f"{time_col_xpath}/preceding-sibling::td")) + 1
        table_class = '//table[@class="torrents"]' if html.xpath("//table[@class=\"torrents\"]") else ""
        rows = html.xpath(f"{table_class}//tr[position()>1]")
        weight_col = None
        header_cells = html.xpath(f"{table_class}//thead//th")
        if not header_cells:
            header_cells = html.xpath(f"{table_class}//tr[1]/td")
        for j, hc in enumerate(header_cells):
            htext = (hc.xpath("string(.)") or "").strip()
            if "零魔" in htext or "权重" in htext or "系数" in htext:
                weight_col = j + 1
                break
        seeding_sizes = html.xpath(f"{table_class}//tr[position()>1]/td[{size_col}]")
        seeding_seeders = html.xpath(f"{table_class}//tr[position()>1]/td[{seeders_col}]/b/a/text()")
        if not seeding_seeders:
            seeding_seeders = html.xpath(f"{table_class}//tr[position()>1]/td[{seeders_col}]//text()")
        seeding_times = []
        if time_col:
            time_tds = html.xpath(f"{table_class}//tr[position()>1]/td[{time_col}]")
            if time_tds:
                seeding_times = [td.xpath("string(.)") for td in time_tds]
            if not seeding_times:
                span_titles = html.xpath(f'{table_class}//tr[position()>1]/td[{time_col}]//span/@title')
                if span_titles:
                    seeding_times = span_titles
        page_seeding_detail = []
        if seeding_sizes and seeding_seeders:
            for i in range(0, len(seeding_sizes)):
                size = StringUtils.num_filesize(seeding_sizes[i].xpath("string(.)").strip())
                seeders = StringUtils.str_int(seeding_seeders[i])
                pubdate = None
                if i < len(seeding_times) and seeding_times[i]:
                    pubdate_raw = str(seeding_times[i]).strip()
                    pubdate = pubdate_raw
                    if pubdate:
                        pubdate = pubdate.replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ").strip()
                        _len = len(pubdate)
                        _need_space = _len >= 18 and _len > 10 and pubdate[10] != " "
                        if _need_space:
                            pubdate = pubdate[:10] + " " + pubdate[10:]
                torrent_id = None
                name = None
                seed_weight = 1.0
                if i < len(rows):
                    row = rows[i]
                    if weight_col is not None:
                        w_cells = row.xpath(f"./td[{weight_col}]")
                        if w_cells:
                            w_text = (w_cells[0].xpath("string(.)") or "").strip()
                            if "0.2" in w_text or "零魔" in w_text:
                                seed_weight = 0.2
                            else:
                                try:
                                    seed_weight = float(w_text) if w_text else 1.0
                                except (ValueError, TypeError):
                                    pass
                    details_href = row.xpath('.//a[contains(@href,"details.php?id=")]/@href')
                    if details_href:
                        tid_match = re.search(r"details\.php\?id=(\d+)", details_href[0])
                        if tid_match:
                            torrent_id = tid_match.group(1)
                    detail_path = details_href[0].strip() if details_href else None
                    details_title = row.xpath('.//a[contains(@href,"details.php?id=")]/@title')
                    if details_title and details_title[0]:
                        name = str(details_title[0]).strip()
                    else:
                        details_text = row.xpath('.//a[contains(@href,"details.php?id=")]//text()')
                        if details_text and details_text[0]:
                            name = str(details_text[0]).strip()
                detail_item = {
                    "seeders": seeders,
                    "size": size,
                    "pubdate": pubdate,
                    "torrent_id": torrent_id,
                    "name": name or "—",
                }
                if detail_path:
                    detail_item["detail_path"] = detail_path
                if weight_col is not None:
                    detail_item["weight"] = seed_weight
                page_seeding_detail.append(detail_item)
        next_page = None
        next_page_text = html.xpath(
            '//a[contains(.//text(), "下一页") or contains(.//text(), "下一頁") or contains(.//text(), ">")]/@href'
        )
        while next_page_text:
            np = next_page_text.pop().strip()
            if not np.startswith("details.php"):
                next_page = np
                break
    finally:
        if html is not None:
            del html
    return page_seeding_detail, next_page


def extract_userid_from_index_nexusphp(html_text: str) -> Optional[str]:
    """从 NexusPHP 首页或用户页提取 userid"""
    if not html_text:
        return None
    m = re.search(r"userdetails\.php\?id=(\d+)", html_text)
    return m.group(1) if m and m.group(1).strip() else None
