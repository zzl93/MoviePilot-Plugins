# -*- coding: utf-8 -*-
"""PT魔力计算器插件 - 种子主表与每日快照子表数据库操作"""
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import Engine, Base, ScopedSession, db_query, db_update
from app.log import logger

from app.plugins.ptbonuscalc.server.models import SeedInfo, SeedInfoSnapshot


def init_seedinfo_db() -> None:
    """初始化种子主表与快照子表。插件 init_plugin 时调用。"""
    try:
        Base.metadata.create_all(
            bind=Engine,
            tables=[SeedInfo.__table__, SeedInfoSnapshot.__table__],
        )
        _migrate_schema()
    except Exception as e:
        logger.error(f"PT魔力计算器插件：初始化种子信息表失败: {e}", exc_info=True)


def _migrate_schema() -> None:
    """检查并添加缺失字段"""
    for model in (SeedInfo, SeedInfoSnapshot):
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
    """标准化下载器状态：seeding/downloading/paused/error/uploading/deleted/unknown"""
    if not state:
        return "unknown"
    s = str(state).strip().lower()
    for k in ("seeding", "downloading", "paused", "error", "uploading", "deleted"):
        if k in s or s == k:
            return k
    return "unknown"


@db_update
def save_seed_info(
    db: Session,
    site_domain: str,
    torrent_key: str,
    site_data: Dict[str, Any],
    downloader_data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    保存或更新种子主表，并写入当日快照。
    site_data: name, size, pubdate, seed_attr(主辅属性 main/aux)
    downloader_data: hash, tracker, added_on(秒时间戳), downloader(名称), ratio, uploaded, downloaded, seeding_time, state, save_path, category, tags
    """
    from app.plugins.ptbonuscalc.server.tracker_utils import parse_tracker_domain

    mk = _mapping_key(site_domain, torrent_key)
    today = date.today()
    now = datetime.now()
    now_time_str = now.strftime("%H:%M:%S")

    row = db.query(SeedInfo).filter(SeedInfo.mapping_key == mk).first()
    if not row:
        row = SeedInfo(
            mapping_key=mk,
            site_domain=site_domain,
            torrent_key=torrent_key,
            name=site_data.get("name"),
            size=site_data.get("size", 0) or 0,
            pubdate=site_data.get("pubdate"),
        )
        db.add(row)
        db.flush()

    # 更新主表不变字段
    row.name = site_data.get("name") or row.name
    row.size = site_data.get("size", 0) or row.size or 0
    row.pubdate = site_data.get("pubdate") or row.pubdate
    if "seed_attr" in site_data:
        row.seed_attr = site_data.get("seed_attr") or None

    if downloader_data:
        tracker_raw = downloader_data.get("tracker") or downloader_data.get("downloader_tracker") or ""
        tracker_orig, tracker_domain = parse_tracker_domain(tracker_raw)
        added_on = downloader_data.get("added_on")
        added_dt = datetime.fromtimestamp(added_on) if added_on else None
        row.downloader_hash = downloader_data.get("hash") or downloader_data.get("downloader_hash")
        row.downloader_tracker = tracker_raw or row.downloader_tracker
        row.downloader_tracker_domain = tracker_domain or row.downloader_tracker_domain
        row.downloader_added_at = added_dt or row.downloader_added_at
        # 主从表共有字段：仅当新值与主表当前值不同时才更新主表，否则只更新快照表
        new_name = downloader_data.get("downloader") or downloader_data.get("downloader_name")
        new_category = downloader_data.get("category") or downloader_data.get("downloader_category")
        tags_val = downloader_data.get("tags") or downloader_data.get("downloader_tags")
        new_tags = tags_val if isinstance(tags_val, str) else (",".join(tags_val) if isinstance(tags_val, (list, tuple)) else None)
        if new_name is not None and new_name != row.downloader_name:
            row.downloader_name = new_name
        if new_category is not None and new_category != row.downloader_category:
            row.downloader_category = new_category
        if new_tags is not None and new_tags != row.downloader_tags:
            row.downloader_tags = new_tags

    # 当日快照
    snap = db.query(SeedInfoSnapshot).filter(
        SeedInfoSnapshot.seedinfo_id == row.id,
        SeedInfoSnapshot.snapshot_date == today,
    ).first()
    if not snap:
        snap = SeedInfoSnapshot(
            seedinfo_id=row.id,
            snapshot_date=today,
            snapshot_time=now_time_str,
            web_status="exists",
        )
        db.add(snap)
        db.flush()

    snap.snapshot_time = now_time_str
    snap.web_status = "exists"
    snap.seeders = site_data.get("seeders", 0) or 0
    snap.weight = site_data.get("weight", 1.0) or 1.0
    snap.T_weeks = site_data.get("T_weeks", 0.0) or 0.0
    snap.A_value = site_data.get("A_value", 0.0) or 0.0
    snap.A_per_GB = site_data.get("A_per_GB", 0.0) or 0.0
    snap.bonus_per_hour = site_data.get("bonus_per_hour", 0.0) or 0.0

    if downloader_data:
        snap.downloader_status = _normalize_downloader_state(
            downloader_data.get("state") or downloader_data.get("downloader_state")
        )
        snap.downloader_ratio = downloader_data.get("ratio") or downloader_data.get("downloader_ratio")
        snap.downloader_uploaded = downloader_data.get("uploaded") or downloader_data.get("downloader_uploaded")
        snap.downloader_downloaded = downloader_data.get("downloaded") or downloader_data.get("downloader_downloaded")
        snap.downloader_seeding_time = downloader_data.get("seeding_time") or downloader_data.get("downloader_seeding_time")
        snap.downloader_state = downloader_data.get("state") or downloader_data.get("downloader_state")
        snap.downloader_save_path = downloader_data.get("save_path") or downloader_data.get("downloader_save_path")
        if new_name is not None:
            snap.downloader_name = new_name
        if new_category is not None:
            snap.downloader_category = new_category
        if new_tags is not None:
            snap.downloader_tags = new_tags


@db_update
def remove_seed_info(db: Session, site_domain: str, torrent_key: str) -> None:
    """删除种子（主表删除会级联删除快照）"""
    mk = _mapping_key(site_domain, torrent_key)
    db.query(SeedInfo).filter(SeedInfo.mapping_key == mk).delete()


@db_query
def get_seed_info(db: Session, site_domain: str, torrent_key: str) -> Optional[SeedInfo]:
    """获取单条种子主表记录"""
    mk = _mapping_key(site_domain, torrent_key)
    return db.query(SeedInfo).filter(SeedInfo.mapping_key == mk).first()


@db_query
def get_seed_with_latest_snapshot(
    db: Session, site_domain: str, torrent_key: str
) -> Optional[Tuple[SeedInfo, Optional[SeedInfoSnapshot]]]:
    """获取种子主表及最新快照"""
    seed = get_seed_info(db, site_domain, torrent_key)
    if not seed:
        return None
    snap = (
        db.query(SeedInfoSnapshot)
        .filter(SeedInfoSnapshot.seedinfo_id == seed.id)
        .order_by(SeedInfoSnapshot.snapshot_date.desc())
        .first()
    )
    return (seed, snap)


@db_query
def list_all_mappings(db: Session) -> Dict[str, str]:
    """获取全部站点种子与下载器的关联：{mapping_key: downloader_hash}"""
    rows = db.query(SeedInfo).filter(SeedInfo.downloader_hash.isnot(None)).all()
    return {r.mapping_key: r.downloader_hash for r in rows}


@db_query
def list_seed_info_by_site(db: Session, site_domain: str) -> List[SeedInfo]:
    """按站点获取种子主表列表"""
    return db.query(SeedInfo).filter(SeedInfo.site_domain == site_domain).all()


@db_query
def list_seed_with_latest_snapshot_by_site(
    db: Session, site_domain: str
) -> List[Tuple[SeedInfo, Optional[SeedInfoSnapshot]]]:
    """按站点获取种子主表及各自最新快照"""
    seeds = list_seed_info_by_site(db, site_domain)
    result = []
    for seed in seeds:
        snap = (
            db.query(SeedInfoSnapshot)
            .filter(SeedInfoSnapshot.seedinfo_id == seed.id)
            .order_by(SeedInfoSnapshot.snapshot_date.desc())
            .first()
        )
        result.append((seed, snap))
    return result


@db_update
def delete_seed_info_by_site(db: Session, site_domain: str) -> None:
    """删除指定站点的全部种子（级联删除快照）"""
    db.query(SeedInfo).filter(SeedInfo.site_domain == site_domain).delete()


@db_update
def batch_save_seeding_from_parser(
    db: Session,
    site_domain: str,
    seeding_list: List[Dict[str, Any]],
) -> None:
    """
    将解析得到的做种列表批量写入主表+每日快照。
    网页中存在的种子：主表 upsert，当日快照 web_status=exists。
    主表已存在但本次不在列表中的种子：当日快照 web_status=not_exists。
    """
    today = date.today()
    now = datetime.now()
    now_time_str = now.strftime("%H:%M:%S")

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
        row = db.query(SeedInfo).filter(SeedInfo.mapping_key == mk).first()
        if not row:
            row = SeedInfo(
                mapping_key=mk,
                site_domain=site_domain,
                torrent_key=tkey,
                name=name,
                size=size_b,
                pubdate=s.get("pubdate"),
                seed_attr=s.get("seed_attr") or None,
            )
            db.add(row)
            db.flush()
        elif "seed_attr" in s:
            row.seed_attr = s.get("seed_attr") or None

        snap = db.query(SeedInfoSnapshot).filter(
            SeedInfoSnapshot.seedinfo_id == row.id,
            SeedInfoSnapshot.snapshot_date == today,
        ).first()
        if not snap:
            snap = SeedInfoSnapshot(
                seedinfo_id=row.id,
                snapshot_date=today,
                snapshot_time=now_time_str,
                web_status="exists",
                seeders=s.get("seeders", 0) or 0,
                weight=s.get("weight", 1.0) or 1.0,
            )
            db.add(snap)
        else:
            snap.snapshot_time = now_time_str
            snap.web_status = "exists"
            snap.seeders = s.get("seeders", 0) or 0
            snap.weight = s.get("weight", 1.0) or 1.0

    # 对主表已存在、本次不在列表中的种子写入 not_exists 快照
    existing = db.query(SeedInfo).filter(SeedInfo.site_domain == site_domain).all()
    for row in existing:
        if row.torrent_key in seen_keys:
            continue
        snap = db.query(SeedInfoSnapshot).filter(
            SeedInfoSnapshot.seedinfo_id == row.id,
            SeedInfoSnapshot.snapshot_date == today,
        ).first()
        if not snap:
            snap = SeedInfoSnapshot(
                seedinfo_id=row.id,
                snapshot_date=today,
                snapshot_time=now_time_str,
                web_status="not_exists",
            )
            db.add(snap)
