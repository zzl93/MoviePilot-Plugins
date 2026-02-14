# -*- coding: utf-8 -*-
"""PT魔力计算器插件 - 四张表（站点种子主/快照、下载器种子主/快照）数据库操作"""
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import Engine, Base, db_query, db_update
from app.log import logger

from app.plugins.ptbonuscalc.server import utils as ptbonuscalc_utils
from app.plugins.ptbonuscalc.server.models import (
    SiteSeed,
    SiteSeedSnapshot,
    DownloaderSeed,
    DownloaderSeedSnapshot,
)


def init_seedinfo_db() -> None:
    """初始化四张表。插件 init_plugin 时调用。"""
    try:
        Base.metadata.create_all(
            bind=Engine,
            tables=[
                SiteSeed.__table__,
                SiteSeedSnapshot.__table__,
                DownloaderSeed.__table__,
                DownloaderSeedSnapshot.__table__,
            ],
        )
        _migrate_schema()
    except Exception as e:
        logger.error(f"PT魔力计算器插件：初始化种子信息表失败: {e}", exc_info=True)


def _migrate_schema() -> None:
    for model in (SiteSeed, SiteSeedSnapshot, DownloaderSeed, DownloaderSeedSnapshot):
        _migrate_table(model)


def _migrate_table(model: type) -> None:
    try:
        conn = Engine.connect()
        inspector = inspect(conn)
        if not inspector.has_table(model.__tablename__):
            conn.close()
            return
        existing = {c["name"] for c in inspector.get_columns(model.__tablename__)}
        for col in model.__table__.columns:
            if col.name in existing:
                continue
            try:
                ctype = "TEXT"
                if col.type.python_type in (int,):
                    ctype = "INTEGER" if "sqlite" in str(Engine.url).lower() else "BIGINT"
                elif col.type.python_type == float:
                    ctype = "REAL"
                elif col.type.python_type in (datetime, date):
                    ctype = "TIMESTAMP" if col.type.python_type == datetime else "DATE"
                elif col.type.python_type == bool:
                    ctype = "INTEGER"
                add_sql = f'ALTER TABLE {model.__tablename__} ADD COLUMN "{col.name}" {ctype}'
                conn.execute(text(add_sql))
                conn.commit()
                logger.info(f"PT魔力计算器插件：{model.__tablename__} 已添加字段 {col.name}")
            except Exception as add_err:
                if "duplicate" in str(add_err).lower() or "already exists" in str(add_err).lower():
                    pass
                else:
                    logger.warning(f"PT魔力计算器插件：添加字段 {col.name} 失败: {add_err}")
        conn.close()
    except Exception as e:
        logger.warning(f"PT魔力计算器插件：表结构迁移检查失败: {e}")


def _mapping_key(site_domain: str, torrent_key: str) -> str:
    return f"{site_domain}|{torrent_key}"


def _torrent_key(torrent_id: Any, name: str = "", size: Any = 0) -> str:
    return str(torrent_id) if torrent_id else f"{name or ''}|{size or 0}"


def _normalize_downloader_state(state: Optional[str]) -> str:
    if not state:
        return "unknown"
    s = str(state).strip().lower()
    for k in ("seeding", "downloading", "paused", "error", "uploading", "deleted"):
        if k in s or s == k:
            return k
    return "unknown"


# ---------- 站点侧：只写站点主表 + 站点快照 ----------


@db_update
def batch_save_seeding_from_parser(
    db: Session,
    site_domain: str,
    seeding_list: List[Dict[str, Any]],
) -> None:
    """将解析得到的做种列表批量写入站点种子主表与站点种子快照表。"""
    today = date.today()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    seen_keys = set()

    for s in seeding_list:
        if not isinstance(s, dict):
            continue
        size_val = s.get("size") or 0
        size_b = int(size_val) if isinstance(size_val, (int, float)) else 0
        name = s.get("name") or "—"
        torrent_id = s.get("torrent_id")
        tkey = _torrent_key(torrent_id, name, size_b)
        seen_keys.add(tkey)
        mk = _mapping_key(site_domain, tkey)

        row = db.query(SiteSeed).filter(SiteSeed.mapping_key == mk).first()
        if not row:
            row = SiteSeed(
                mapping_key=mk,
                site_domain=site_domain,
                torrent_id=tkey,
                name=name,
                size=size_b,
                pubdate=s.get("pubdate"),
                seed_attr=s.get("seed_attr") or None,
            )
            db.add(row)
            db.flush()
        else:
            row.name = name
            row.size = size_b
            if "pubdate" in s:
                row.pubdate = s.get("pubdate") or row.pubdate
            if "seed_attr" in s:
                row.seed_attr = s.get("seed_attr") or None

        snap = db.query(SiteSeedSnapshot).filter(
            SiteSeedSnapshot.site_seed_id == row.id,
            SiteSeedSnapshot.snapshot_date == today,
        ).first()
        if not snap:
            snap = SiteSeedSnapshot(
                site_seed_id=row.id,
                snapshot_date=today,
                snapshot_time=now_time_str,
                web_status="exists",
                seeders=s.get("seeders", 0) or 0,
                weight=s.get("weight", 1.0) or 1.0,
                T_weeks=s.get("T_weeks", 0.0) or 0.0,
            )
            db.add(snap)
        else:
            snap.snapshot_time = now_time_str
            snap.web_status = "exists"
            snap.seeders = s.get("seeders", 0) or 0
            snap.weight = s.get("weight", 1.0) or 1.0
            snap.T_weeks = s.get("T_weeks", 0.0) or 0.0

    existing = db.query(SiteSeed).filter(SiteSeed.site_domain == site_domain).all()
    for row in existing:
        if row.torrent_id in seen_keys:
            continue
        snap = db.query(SiteSeedSnapshot).filter(
            SiteSeedSnapshot.site_seed_id == row.id,
            SiteSeedSnapshot.snapshot_date == today,
        ).first()
        if not snap:
            snap = SiteSeedSnapshot(
                site_seed_id=row.id,
                snapshot_date=today,
                snapshot_time=now_time_str,
                web_status="not_exists",
            )
            db.add(snap)


@db_query
def list_site_seeds_with_latest_snapshot_by_site(
    db: Session, site_domain: str
) -> List[Tuple[SiteSeed, Optional[SiteSeedSnapshot]]]:
    """按站点获取站点种子主表及各自最新站点快照。"""
    seeds = db.query(SiteSeed).filter(SiteSeed.site_domain == site_domain).all()
    result = []
    for seed in seeds:
        snap = (
            db.query(SiteSeedSnapshot)
            .filter(SiteSeedSnapshot.site_seed_id == seed.id)
            .order_by(SiteSeedSnapshot.snapshot_date.desc())
            .first()
        )
        result.append((seed, snap))
    return result


@db_update
def delete_seed_info_by_site(db: Session, site_domain: str) -> None:
    """删除指定站点的全部站点种子（级联删除站点快照；下载器种子 site_seed_id 置空）。"""
    db.query(SiteSeed).filter(SiteSeed.site_domain == site_domain).delete()


@db_query
def get_site_seed(db: Session, site_domain: str, torrent_key: str) -> Optional[SiteSeed]:
    mk = _mapping_key(site_domain, torrent_key)
    return db.query(SiteSeed).filter(SiteSeed.mapping_key == mk).first()


def _get_latest_site_snapshot(db: Session, site_seed_id: int) -> Optional[SiteSeedSnapshot]:
    return (
        db.query(SiteSeedSnapshot)
        .filter(SiteSeedSnapshot.site_seed_id == site_seed_id)
        .order_by(SiteSeedSnapshot.snapshot_date.desc())
        .first()
    )


def _get_downloader_seed_by_site_seed_id(db: Session, site_seed_id: int) -> Optional[DownloaderSeed]:
    return db.query(DownloaderSeed).filter(DownloaderSeed.site_seed_id == site_seed_id).first()


@db_query
def get_downloader_seed_by_hash(
    db: Session,
    downloader_hash: str,
    allowed_downloader_names: Optional[List[str]] = None,
) -> Optional[Tuple[DownloaderSeed, Optional[DownloaderSeedSnapshot]]]:
    """按 hash 从表查一条下载器种子及最新快照；allowed_downloader_names 非空时只在该列表内查。用于保存关联时从表取详情。"""
    q = db.query(DownloaderSeed).filter(DownloaderSeed.downloader_hash == downloader_hash)
    if allowed_downloader_names is not None and len(allowed_downloader_names) > 0:
        q = q.filter(DownloaderSeed.downloader_name.in_(allowed_downloader_names))
    row = q.first()
    if not row:
        return None
    snap = _get_latest_downloader_snapshot(db, row.id)
    return (row, snap)


def _get_latest_downloader_snapshot(db: Session, downloader_seed_id: int) -> Optional[DownloaderSeedSnapshot]:
    return (
        db.query(DownloaderSeedSnapshot)
        .filter(DownloaderSeedSnapshot.downloader_seed_id == downloader_seed_id)
        .order_by(DownloaderSeedSnapshot.snapshot_date.desc())
        .first()
    )


# ---------- 下载器侧：主表 + 快照，关联 site_seed_id ----------


@db_update
def upsert_downloader_seed(
    db: Session,
    downloader_name: str,
    downloader_hash: str,
    downloader_data: Dict[str, Any],
    site_seed_id: Optional[int] = None,
) -> Optional[DownloaderSeed]:
    """写入或更新下载器种子主表及当日快照；可设置 site_seed_id 关联。"""
    from app.plugins.ptbonuscalc.server.utils import parse_tracker_domain

    today = date.today()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    tracker_raw = downloader_data.get("tracker") or downloader_data.get("downloader_tracker") or ""
    _, tracker_domain = parse_tracker_domain(tracker_raw)
    added_on = downloader_data.get("added_on")
    added_dt = datetime.fromtimestamp(added_on) if added_on else None
    size_val = downloader_data.get("total_size") or downloader_data.get("downloader_size") or downloader_data.get("size")
    size_b = int(size_val) if size_val is not None else 0

    row = (
        db.query(DownloaderSeed)
        .filter(
            DownloaderSeed.downloader_name == downloader_name,
            DownloaderSeed.downloader_hash == downloader_hash,
        )
        .first()
    )
    seed_name = downloader_data.get("name") or downloader_data.get("torrent_name") or ""
    if not row:
        row = DownloaderSeed(
            downloader_hash=downloader_hash,
            downloader_name=downloader_name,
            downloader_torrent_name=seed_name or None,
            size=size_b,
            tracker=tracker_raw,
            tracker_domain=tracker_domain or None,
            added_at=added_dt,
            site_seed_id=site_seed_id,
            seed_attr=downloader_data.get("seed_attr") or None,
            main_hash=downloader_data.get("main_hash") or None,
            main_tracker_domain=downloader_data.get("main_tracker_domain") or None,
        )
        db.add(row)
        db.flush()
    else:
        row.downloader_torrent_name = seed_name or row.downloader_torrent_name
        row.size = size_b if size_b else row.size
        row.tracker = tracker_raw or row.tracker
        row.tracker_domain = tracker_domain or row.tracker_domain
        row.added_at = added_dt or row.added_at
        if site_seed_id is not None:
            row.site_seed_id = site_seed_id
        if "seed_attr" in downloader_data:
            row.seed_attr = downloader_data.get("seed_attr") or None
        if "main_hash" in downloader_data:
            row.main_hash = downloader_data.get("main_hash") or None
        if "main_tracker_domain" in downloader_data:
            row.main_tracker_domain = downloader_data.get("main_tracker_domain") or None

    snap = db.query(DownloaderSeedSnapshot).filter(
        DownloaderSeedSnapshot.downloader_seed_id == row.id,
        DownloaderSeedSnapshot.snapshot_date == today,
    ).first()
    if not snap:
        snap = DownloaderSeedSnapshot(
            downloader_seed_id=row.id,
            snapshot_date=today,
            snapshot_time=now_time_str,
        )
        db.add(snap)
        db.flush()
    snap.snapshot_time = now_time_str
    snap.downloader_status = _normalize_downloader_state(
        downloader_data.get("state") or downloader_data.get("downloader_state")
    )
    snap.ratio = downloader_data.get("ratio") or downloader_data.get("downloader_ratio")
    snap.uploaded = downloader_data.get("uploaded") or downloader_data.get("downloader_uploaded")
    snap.downloaded = downloader_data.get("downloaded") or downloader_data.get("downloader_downloaded")
    snap.seeding_time = downloader_data.get("seeding_time") or downloader_data.get("downloader_seeding_time")
    snap.save_path = downloader_data.get("save_path") or downloader_data.get("downloader_save_path")
    tags_val = downloader_data.get("tags") or downloader_data.get("downloader_tags")
    snap.tags = tags_val if isinstance(tags_val, str) else (",".join(tags_val) if isinstance(tags_val, (list, tuple)) else None)
    snap.category = downloader_data.get("category") or downloader_data.get("downloader_category")
    return row


@db_update
def clear_downloader_seed_site_seed_id(db: Session, site_seed_id: int) -> None:
    """将关联到该站点种子的下载器种子的 site_seed_id 置空（取消关联）。"""
    db.query(DownloaderSeed).filter(DownloaderSeed.site_seed_id == site_seed_id).update({"site_seed_id": None})


@db_query
def list_downloader_seeds_with_latest_snapshot(
    db: Session,
    allowed_downloader_names: Optional[List[str]] = None,
) -> List[Tuple[DownloaderSeed, Optional[DownloaderSeedSnapshot]]]:
    """
    从表里按所属下载器查所有下载器种子及各自最新快照。
    allowed_downloader_names 非空时只查该列表内的下载器；用于 Page 展示（只读表，不调下载器 API）。
    """
    q = db.query(DownloaderSeed)
    if allowed_downloader_names is not None and len(allowed_downloader_names) > 0:
        q = q.filter(DownloaderSeed.downloader_name.in_(allowed_downloader_names))
    seeds = q.all()
    result = []
    for seed in seeds:
        snap = _get_latest_downloader_snapshot(db, seed.id)
        result.append((seed, snap))
    return result


@db_update
def batch_upsert_downloader_torrents(
    db: Session,
    downloader_name: str,
    torrents_list: List[Dict[str, Any]],
) -> None:
    """
    将当前配置的某下载器下的全部种子写入下载器种子主表与快照表（不覆盖已有 site_seed_id）。
    torrents_list 每项为 dict，需含 hash、name、total_size、tracker、ratio 等（与 _fetch_downloader_torrents 单条格式一致）。
    """
    for dt in torrents_list:
        if not isinstance(dt, dict):
            continue
        h = dt.get("hash")
        if not h:
            continue
        upsert_downloader_seed(db, downloader_name, h, dt, site_seed_id=None)


# ---------- 兼容旧接口：list_seed_with_latest_snapshot_by_site / get_seed_with_latest_snapshot / save_seed_info / remove_seed_info / list_all_mappings ----------


@db_query
def list_seed_with_latest_snapshot_by_site(
    db: Session,
    site_domain: str,
    allowed_downloader_names: Optional[List[str]] = None,
) -> List[Tuple[Any, Any, Any, Any]]:
    """
    按站点返回：站点种子+最新站点快照+关联的下载器种子+最新下载器快照（若无关联则后两项为 None）。
    allowed_downloader_names 非空时，仅当关联的下载器种子所属下载器在该列表中时才展示右列，否则右列为 None（Page 按当前配置过滤）。
    """
    pairs = list_site_seeds_with_latest_snapshot_by_site(db, site_domain)
    result = []
    for site_seed, site_snap in pairs:
        dl_seed = _get_downloader_seed_by_site_seed_id(db, site_seed.id)
        if dl_seed and allowed_downloader_names is not None and dl_seed.downloader_name not in allowed_downloader_names:
            dl_seed, dl_snap = None, None
        else:
            dl_snap = _get_latest_downloader_snapshot(db, dl_seed.id) if dl_seed else None
        result.append((site_seed, site_snap, dl_seed, dl_snap))
    return result


@db_query
def get_seed_with_latest_snapshot(
    db: Session, site_domain: str, torrent_key: str
) -> Optional[Tuple[Any, Any, Any, Any]]:
    """获取站点种子+最新站点快照+关联的下载器种子+最新下载器快照（若无则后两项为 None）。"""
    site_seed = get_site_seed(db, site_domain, torrent_key)
    if not site_seed:
        return None
    site_snap = _get_latest_site_snapshot(db, site_seed.id)
    dl_seed = _get_downloader_seed_by_site_seed_id(db, site_seed.id)
    dl_snap = _get_latest_downloader_snapshot(db, dl_seed.id) if dl_seed else None
    return (site_seed, site_snap, dl_seed, dl_snap)


@db_update
def save_seed_info(
    db: Session,
    site_domain: str,
    torrent_key: str,
    site_data: Dict[str, Any],
    downloader_data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    站点侧：有 site_data 则 upsert 站点主表+当日站点快照（仅站点字段）。
    下载器侧：有 downloader_data 则 upsert 下载器主表+当日下载器快照，并设 site_seed_id 关联到该站点种子。
    """
    site_seed = get_site_seed(db, site_domain, torrent_key)
    if not site_seed and site_data:
        mk = _mapping_key(site_domain, torrent_key)
        site_seed = SiteSeed(
            mapping_key=mk,
            site_domain=site_domain,
            torrent_id=torrent_key,
            name=site_data.get("name"),
            size=int(site_data.get("size") or 0),
            pubdate=site_data.get("pubdate"),
            seed_attr=site_data.get("seed_attr") or None,
        )
        db.add(site_seed)
        db.flush()
    if site_seed and site_data:
        site_seed.name = site_data.get("name") or site_seed.name
        site_seed.size = int(site_data.get("size") or 0) or site_seed.size
        if "pubdate" in site_data:
            site_seed.pubdate = site_data.get("pubdate") or site_seed.pubdate
        if "seed_attr" in site_data:
            site_seed.seed_attr = site_data.get("seed_attr") or None
        today = date.today()
        now_time_str = datetime.now().strftime("%H:%M:%S")
        snap = db.query(SiteSeedSnapshot).filter(
            SiteSeedSnapshot.site_seed_id == site_seed.id,
            SiteSeedSnapshot.snapshot_date == today,
        ).first()
        if not snap:
            snap = SiteSeedSnapshot(
                site_seed_id=site_seed.id,
                snapshot_date=today,
                snapshot_time=now_time_str,
                web_status="exists",
                seeders=site_data.get("seeders", 0) or 0,
                weight=site_data.get("weight", 1.0) or 1.0,
                T_weeks=site_data.get("T_weeks", 0.0) or 0.0,
            )
            db.add(snap)
        else:
            snap.seeders = site_data.get("seeders", 0) or 0
            snap.weight = site_data.get("weight", 1.0) or 1.0
            snap.T_weeks = site_data.get("T_weeks", 0.0) or 0.0
            snap.snapshot_time = now_time_str
            snap.web_status = "exists"

    if downloader_data and site_seed:
        downloader_name = downloader_data.get("downloader") or downloader_data.get("downloader_name") or ""
        if not downloader_name and db and getattr(db, "query", None):
            pass
        hash_val = downloader_data.get("hash") or downloader_data.get("downloader_hash")
        if not hash_val:
            return
        if not downloader_name:
            downloader_name = "qb"  # 默认，调用方应传入
        upsert_downloader_seed(db, downloader_name, hash_val, downloader_data, site_seed_id=site_seed.id)


@db_update
def remove_seed_info(db: Session, site_domain: str, torrent_key: str) -> None:
    """取消关联：把该站点种子对应的下载器种子的 site_seed_id 置空。不删站点种子与下载器种子记录。"""
    site_seed = get_site_seed(db, site_domain, torrent_key)
    if site_seed:
        clear_downloader_seed_site_seed_id(db, site_seed.id)


@db_update
def save_torrent_mapping(
    db: Session,
    site_domain: str,
    torrent_key: str,
    downloader_hash: str,
    sync_downloaders: List[str],
    primary_downloaders: Optional[List[str]] = None,
    aux_downloaders: Optional[List[str]] = None,
    site_data: Optional[Dict[str, Any]] = None,
    downloader_data: Optional[Dict[str, Any]] = None,
) -> None:
    """保存站点种子与下载器种子的关联：若无 site_data/downloader_data 则从表查，补全 seed_attr 后调用 save_seed_info。"""
    sd = site_data
    if not sd:
        quad = get_seed_with_latest_snapshot(db, site_domain, torrent_key)
        if quad:
            site_seed, site_snap, _dl_seed, _dl_snap = quad
            sd = {
                "name": site_seed.name,
                "size": site_seed.size,
                "pubdate": site_seed.pubdate,
                "seeders": site_snap.seeders if site_snap else 0,
                "weight": site_snap.weight if site_snap else 1.0,
            }
    if not sd:
        sd = {"torrent_key": torrent_key}
    dd = downloader_data
    if not dd and downloader_hash and sync_downloaders:
        try:
            pair = get_downloader_seed_by_hash(db, downloader_hash, sync_downloaders)
            if pair:
                dl_seed, dl_snap = pair
                dd = {
                    "hash": dl_seed.downloader_hash,
                    "downloader": dl_seed.downloader_name,
                    "name": getattr(dl_seed, "downloader_torrent_name", None) or "",
                    "total_size": dl_seed.size or 0,
                    "tracker": dl_seed.tracker or "",
                    "ratio": (dl_snap.ratio if dl_snap else None) or 0.0,
                    "uploaded": (dl_snap.uploaded if dl_snap else None) or 0,
                    "downloaded": (dl_snap.downloaded if dl_snap else None) or 0,
                    "seeding_time": (dl_snap.seeding_time if dl_snap else None) or 0,
                    "state": (dl_snap.downloader_status if dl_snap else None) or "",
                    "save_path": (dl_snap.save_path if dl_snap else None) or "",
                    "category": (dl_snap.category if dl_snap else None) or "",
                    "tags": (dl_snap.tags if dl_snap else None) or "",
                    "added_on": int(dl_seed.added_at.timestamp()) if getattr(dl_seed, "added_at", None) else None,
                }
        except Exception:
            pass
    if not dd and downloader_hash:
        dd = {"hash": downloader_hash}
    if dd and not dd.get("hash"):
        dd["hash"] = downloader_hash
    if dd and not dd.get("downloader") and not dd.get("downloader_name"):
        dd = dict(dd)
        dd["downloader_name"] = "qb"
    if dd and "seed_attr" not in dd:
        dd = dict(dd)
        _dn = dd.get("downloader") or dd.get("downloader_name") or ""
        dd["seed_attr"] = "main" if _dn in (primary_downloaders or []) else ("aux" if _dn in (aux_downloaders or []) else None)
    save_seed_info(db, site_domain, torrent_key, sd, dd)


@db_query
def list_all_mappings(
    db: Session,
    allowed_downloader_names: Optional[List[str]] = None,
) -> Dict[str, str]:
    """获取站点种子与下载器 hash 的关联：{mapping_key: downloader_hash}。allowed_downloader_names 非空时只返回所属下载器在该列表中的记录（Page 按当前配置过滤）。"""
    q = (
        db.query(SiteSeed.mapping_key, DownloaderSeed.downloader_hash)
        .join(DownloaderSeed, DownloaderSeed.site_seed_id == SiteSeed.id)
        .filter(DownloaderSeed.downloader_hash.isnot(None))
    )
    if allowed_downloader_names is not None:
        q = q.filter(DownloaderSeed.downloader_name.in_(allowed_downloader_names))
    rows = q.all()
    return {r[0]: r[1] for r in rows}


def apply_downloader_to_row(
    row_data: Dict[str, Any],
    dt_fresh: Dict[str, Any],
    defaults: Optional[Dict[str, Any]] = None,
) -> None:
    """用下载器种子数据填充展示行 row_data 的 downloader_* 字段（业务：做种展示行）。defaults 为空时仅用 dt_fresh，否则空值用 defaults 兜底。"""
    d = defaults if defaults is not None else {}
    tags_val = dt_fresh.get("tags")
    tags_str = (
        tags_val
        if isinstance(tags_val, str)
        else (",".join(tags_val) if isinstance(tags_val, (list, tuple)) else "")
        if tags_val
        else d.get("downloader_tags", "")
    )
    added_on = dt_fresh.get("added_on")
    row_data["downloader_name"] = dt_fresh.get("downloader") or d.get("downloader_name", "")
    row_data["downloader_torrent_name"] = dt_fresh.get("name") or d.get("downloader_torrent_name", "")
    row_data["downloader_size"] = int(dt_fresh.get("total_size") or 0) or int(d.get("downloader_size") or 0)
    row_data["downloader_tracker"] = dt_fresh.get("tracker") or d.get("downloader_tracker", "")
    row_data["downloader_tracker_domain"] = (
        ptbonuscalc_utils.tracker_domain_group_key(dt_fresh.get("tracker") or "") or d.get("downloader_tracker_domain", "")
    )
    row_data["downloader_ratio"] = float(dt_fresh.get("ratio") or 0.0)
    row_data["downloader_uploaded"] = int(dt_fresh.get("uploaded") or 0)
    row_data["downloader_downloaded"] = int(dt_fresh.get("downloaded") or 0)
    row_data["downloader_seeding_time"] = int(dt_fresh.get("seeding_time") or 0)
    row_data["downloader_state"] = (dt_fresh.get("state") or "") or d.get("downloader_state", "")
    row_data["downloader_status"] = d.get("downloader_status", "")
    row_data["downloader_save_path"] = dt_fresh.get("save_path") or d.get("downloader_save_path", "")
    row_data["downloader_category"] = dt_fresh.get("category") or d.get("downloader_category", "")
    row_data["downloader_tags"] = tags_str
    row_data["downloader_added_at"] = (
        datetime.fromtimestamp(added_on) if added_on else d.get("downloader_added_at")
    )
