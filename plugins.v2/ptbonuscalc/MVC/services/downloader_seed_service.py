"""
下载器表数据查询与初步处理，将 Mapper 返回的 Model 转为业务结构。
与主项目一致：Session 由 Mapper 层 @db_update/@db_query 管理，Service 不持 db，只调用 Mapper。
"""
from typing import List, Optional

from app.plugins.ptbonuscalc.MVC.mappers.downloader_seed_mapper import DownloaderSeedMapper
from app.plugins.ptbonuscalc.MVC.utils.downloader_fetcher import fetch_downloader_torrents
from app.plugins.ptbonuscalc.MVC.utils import keyword_to_domain, tracker_domain_group_key


def _sync_downloaders(plugin) -> List[str]:
    s = getattr(plugin, "sync_downloaders", None) or []
    return s if isinstance(s, list) else []


def _site_address_mappings(plugin) -> dict:
    m = getattr(plugin, "site_address_mappings", None) or {}
    return m if isinstance(m, dict) else {}


def sync_downloader_seeds_from_api(plugin) -> None:
    """根据 plugin.sync_downloaders 从下载器 API 拉取种子并写入插件表；已有关联的 site_seed_id 会保留。"""
    downloader_ids = _sync_downloaders(plugin)
    if not downloader_ids:
        return
    mapper = DownloaderSeedMapper()
    for downloader_id in downloader_ids:
        if not downloader_id:
            continue
        torrents = fetch_downloader_torrents(downloader_id)
        if not torrents:
            continue
        pairs = mapper.list_downloader_seeds_with_latest_snapshot(downloader_ids=[downloader_id])
        existing_site_seed = {seed.hash: seed.site_seed_id for seed, _ in pairs}
        for t in torrents:
            h = t.get("hash") or t.get("info_hash")
            if h and h in existing_site_seed:
                t["site_seed_id"] = existing_site_seed[h]
        mapper.batch_upsert_downloader_torrents(downloader_id=downloader_id, torrent_list=torrents)


def list_downloader_seeds_with_snapshot(
    plugin,
    tracker_domains: Optional[List[str]] = None,
) -> List[dict]:
    """按 plugin.sync_downloaders 调 mapper，可选 tracker_domains 筛选，返回字典列表。"""
    downloader_ids = _sync_downloaders(plugin)
    if not downloader_ids:
        return []
    mapper = DownloaderSeedMapper()
    pairs = mapper.list_downloader_seeds_with_latest_snapshot(
        downloader_ids=downloader_ids,
        tracker_domains=tracker_domains,
    )
    return [
        {
            "id": seed.id,
            "downloader_id": seed.downloader_id,
            "hash": seed.hash,
            "site_seed_id": seed.site_seed_id,
            "name": seed.name,
            "total_size": snap.total_size if snap else 0,
            "ratio": snap.ratio if snap else 0.0,
            "state": snap.state if snap else None,
            "tracker": snap.tracker if snap else None,
        }
        for seed, snap in pairs
    ]


def list_downloader_candidates_for_site(plugin, site_domain: str) -> List[dict]:
    """Filter downloader candidates according to site mappings and tracker domains."""
    mappings = _site_address_mappings(plugin)
    keywords = mappings.get(site_domain) or mappings.get(str(site_domain)) or []
    if isinstance(keywords, str):
        keywords = [keywords]
    tracker_domains: List[str] = []
    for kw in keywords:
        if not kw or not isinstance(kw, str):
            continue
        domain = keyword_to_domain(kw)
        if domain:
            tracker_domains.append(domain.lower())
    rows = list_downloader_seeds_with_snapshot(plugin)
    if tracker_domains:
        domain_set = set(tracker_domains)
        rows = [
            r for r in rows
            if tracker_domain_group_key(r.get("tracker")) in domain_set
        ]
    return [
        {
            "hash": r["hash"],
            "name": r["name"],
            "total_size": r["total_size"],
            "ratio": r["ratio"],
            "tracker": r["tracker"],
            "downloader": r["downloader_id"],
        }
        for r in rows
    ]


def get_downloader_seed_by_hash(
    plugin,
    downloader_hash: str = "",
    downloader_id: Optional[str] = None,
) -> Optional[dict]:
    """按 hash 查单条及最新快照，转为业务结构后返回。"""
    mapper = DownloaderSeedMapper()
    pair = mapper.get_downloader_seed_by_hash(downloader_hash=downloader_hash, downloader_id=downloader_id)
    if not pair:
        return None
    seed, snap = pair
    return {
        "id": seed.id,
        "downloader_id": seed.downloader_id,
        "hash": seed.hash,
        "site_seed_id": seed.site_seed_id,
        "name": seed.name,
        "total_size": snap.total_size if snap else 0,
        "ratio": snap.ratio if snap else 0.0,
        "state": snap.state if snap else None,
        "tracker": snap.tracker if snap else None,
    }
