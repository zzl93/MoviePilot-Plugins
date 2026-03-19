"""
做种数据、关联 payload、保存/取消关联。
"""
from typing import Optional, List, Any
from app.plugins.ptbonuscalc.MVC.mappers.site_seed_mapper import SiteSeedMapper
from app.plugins.ptbonuscalc.MVC.mappers.downloader_seed_mapper import DownloaderSeedMapper
from app.plugins.ptbonuscalc.MVC.services import site_service
from app.plugins.ptbonuscalc.MVC.services import downloader_seed_service
from app.plugins.ptbonuscalc.MVC.utils import calc_bonus_per_hour
from app.db.plugindata_oper import PluginDataOper


def _plugin_id(plugin) -> str:
    return getattr(plugin, "plugin_id", None) or type(plugin).__name__


def get_bonus_seeding_data(
    plugin,
    site_id: Optional[int] = None,
    use_plugindata_only: bool = False,
) -> List[dict]:
    """站点列表 + 每站 pairs；取 bonus_params、address_mappings，用 downloader_seed_service 得候选。算魔力、matched，拼 row 与 downloader_candidates，汇总 total_*。站点数据仅由主项目 SiteRefreshed 事件触发同步。"""
    if use_plugindata_only:
        sites = site_service.get_sites_from_plugindata(plugin, site_id=site_id)
    else:
        indexers = site_service.get_sites_to_query(plugin, site_id=site_id)
        sites = []
        for idx in indexers:
            domain = idx.get("domain") or ""
            site_block = {
                "id": idx.get("id"),
                "name": idx.get("name"),
                "domain": domain,
                "schema": idx.get("parser") or "NexusPHP",
                "bonus_params": {},
                "address_mappings": (getattr(plugin, "site_address_mappings", None) or {}).get(domain) or [],
            }
            sites.append(site_block)
    result_blocks = []
    site_mapper = SiteSeedMapper()
    for site_block in sites:
        sid = site_block.get("id")
        domain = site_block.get("domain") or ""
        pairs = site_mapper.list_seed_with_latest_snapshot_by_site(site_id=sid) if sid is not None else []
        candidates = downloader_seed_service.list_downloader_candidates_for_site(plugin, domain)
        bonus_params = site_block.get("bonus_params") or {}
        rows = []
        total_seed_count = 0
        total_bonus_per_hour = 0.0
        for item in pairs:
            site_seed = item.get("site_seed")
            site_snap = item.get("site_snapshot")
            down_seed = item.get("downloader_seed")
            down_snap = item.get("downloader_snapshot")
            if not site_seed:
                continue
            size = (site_snap.size or 0) if site_snap else 0
            seed_time = (site_snap.seed_time or 0) if site_snap else 0
            extra = site_snap.extra if site_snap else None
            bonus_per_hour = (site_snap.bonus_per_hour or 0.0) if site_snap else 0.0
            if bonus_per_hour == 0 and bonus_params and seed_time and size:
                bonus_per_hour = calc_bonus_per_hour(
                    size,
                    seed_time,
                    bonus_params,
                    seeders=(extra or {}).get("seeders") if isinstance(extra, dict) else None,
                    weight=(extra or {}).get("weight") if isinstance(extra, dict) else None,
                )
            matched = bool(down_seed)
            total_seed_count += 1
            total_bonus_per_hour += bonus_per_hour
            rows.append({
                "site_seed_id": site_seed.id,
                "name": site_seed.name,
                "size": size,
                "seed_time": seed_time,
                "bonus_per_hour": bonus_per_hour,
                "matched": matched,
                "downloader_hash": down_seed.hash if down_seed else None,
                "downloader_seed_id": down_seed.id if down_seed else None,
            })
        result_blocks.append({
            "site": site_block,
            "rows": rows,
            "downloader_candidates": candidates,
            "total_seed_count": total_seed_count,
            "total_bonus_per_hour": round(total_bonus_per_hour, 2),
        })
    return result_blocks


def build_seed_association_payload(
    plugin,
    override_config: Optional[dict] = None,
    site_id: Optional[int] = None,
    keyword: Optional[str] = None,
) -> dict:
    """若有 override_config 临时覆盖 plugin 配置。调 get_bonus_seeding_data；筛未匹配、按 site_fully_matched/selected_sites 过滤；拼 options、right_table。返回 sites、downloader_torrents、downloader_torrents_by_site。"""
    saved = {}
    if override_config:
        for k, v in override_config.items():
            saved[k] = getattr(plugin, k, None)
            setattr(plugin, k, v)
    try:
        downloader_seed_service.sync_downloader_seeds_from_api(plugin)
        blocks = get_bonus_seeding_data(plugin, site_id=site_id, use_plugindata_only=False)
    finally:
        for k, saved_v in saved.items():
            if saved_v is not None:
                setattr(plugin, k, saved_v)
            elif hasattr(plugin, k):
                delattr(plugin, k)
    sites = [b["site"] for b in blocks]
    downloader_torrents_by_site = {b["site"].get("domain") or b["site"].get("id"): b["downloader_candidates"] for b in blocks}
    all_candidates = []
    for b in blocks:
        all_candidates.extend(b["downloader_candidates"])
    seen = set()
    downloader_torrents = []
    for c in all_candidates:
        h = c.get("hash")
        if h and h not in seen:
            seen.add(h)
            downloader_torrents.append(c)
    return {
        "sites": sites,
        "downloader_torrents": downloader_torrents,
        "downloader_torrents_by_site": downloader_torrents_by_site,
        "blocks": blocks,
    }


def apply_save_data(plugin, associations: List[dict]) -> tuple:
    """遍历 associations（每项含 downloader_hash、site_seed_id 等），调 downloader_seed_mapper 保存；site_seed_id 为空即取消关联。返回 (success, message)。"""
    if not associations:
        return True, "无数据需要保存"
    downloader_ids = getattr(plugin, "sync_downloaders", None) or []
    if not isinstance(downloader_ids, list):
        downloader_ids = []
    mapper = DownloaderSeedMapper()
    rows = []
    for item in associations:
        downloader_id = item.get("downloader_id") or item.get("downloader") or (downloader_ids[0] if downloader_ids else "")
        h = item.get("downloader_hash") or item.get("hash")
        if not h or not downloader_id:
            continue
        rows.append(
            {
                "downloader_id": downloader_id,
                "hash": h,
                "snapshot_data": item.get("snapshot_data") or {"total_size": item.get("total_size", 0), "ratio": item.get("ratio", 0), "tracker": item.get("tracker"), "state": item.get("state")},
                "site_seed_id": item.get("site_seed_id"),
                "name": item.get("name"),
            }
        )
    if not rows:
        return True, "无有效数据需要保存"
    try:
        mapper.bulk_upsert_downloader_seeds(rows=rows)
        return True, "保存成功"
    except Exception as e:
        return False, str(e)


def sync_init_mappings_and_fully_matched(plugin, config: dict) -> None:
    """从 config 解析 torrent_mapping_*，与 list_all_mappings 比较，将差异通过 mapper 写回；再根据 get_bonus_seeding_data 写 site_fully_matched。"""
    pid = _plugin_id(plugin)
    pdo = PluginDataOper()
    mapper = DownloaderSeedMapper()
    current = {f"{m['downloader_id']}_{m['downloader_hash']}": m for m in mapper.list_all_mappings()}
    target = {}
    for k, v in (config or {}).items():
        if isinstance(k, str) and k.startswith("torrent_mapping_") and isinstance(v, dict):
            target[f"{v.get('downloader_id')}_{v.get('downloader_hash')}"] = v
    rows = []
    for key, val in target.items():
        if key in current and current[key].get("site_seed_id") == val.get("site_seed_id"):
            continue
        rows.append(
            {
                "downloader_id": val.get("downloader_id", ""),
                "hash": val.get("downloader_hash", ""),
                "snapshot_data": val.get("snapshot_data") or {},
                "site_seed_id": val.get("site_seed_id"),
            }
        )
    if rows:
        mapper.bulk_upsert_downloader_seeds(rows=rows)
    blocks = get_bonus_seeding_data(plugin, use_plugindata_only=False)
    fully_matched = {}
    for b in blocks:
        site_domain = (b["site"].get("domain") or str(b["site"].get("id")) or "")
        rows = b.get("rows") or []
        fully_matched[site_domain] = all(r.get("matched") for r in rows) if rows else False
    pdo.save(pid, "site_fully_matched", fully_matched)
