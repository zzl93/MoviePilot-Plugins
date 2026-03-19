"""
仅操作 DownloaderSeed、DownloaderSeedSnapshot；入参可带 site_seed_id，写入时一并落库。
与主项目一致：继承 DbOper，Mapper 方法通过 @db_update/@db_query 接收注入的 db。
"""
from typing import List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from app.db import DbOper, db_update, db_query
from app.plugins.ptbonuscalc.MVC.models import DownloaderSeed, DownloaderSeedSnapshot


class DownloaderSeedMapper(DbOper):
    def __init__(self, db: Session = None):
        super().__init__(db)

    def _get_latest_downloader_snapshot(
        self, downloader_seed_id: int, db: Session
    ) -> Optional[DownloaderSeedSnapshot]:
        """查指定 DownloaderSeed 的最新一条 DownloaderSeedSnapshot。"""
        return (
            db.query(DownloaderSeedSnapshot)
            .filter(DownloaderSeedSnapshot.downloader_seed_id == downloader_seed_id)
            .order_by(desc(DownloaderSeedSnapshot.id))
            .first()
        )

    def _get_downloader_seed_by_site_seed_id(
        self, site_seed_id: int, db: Session
    ) -> Optional[Tuple[DownloaderSeed, Optional[DownloaderSeedSnapshot]]]:
        """按 site_seed_id 查已关联到该站点种子的 DownloaderSeed 及最新快照。"""
        seed = db.query(DownloaderSeed).filter(
            DownloaderSeed.site_seed_id == site_seed_id
        ).first()
        if not seed:
            return None
        snap = self._get_latest_downloader_snapshot(seed.id, db)
        return (seed, snap)

    @db_query
    def get_downloader_seed_by_hash(
        self,
        db: Session = None,
        downloader_hash: str = "",
        downloader_id: Optional[str] = None,
    ) -> Optional[Tuple[DownloaderSeed, Optional[DownloaderSeedSnapshot]]]:
        """按 hash 查单条 DownloaderSeed 及其最新 DownloaderSeedSnapshot。若指定 downloader_id 则同时匹配。"""
        session = db or self._db
        q = session.query(DownloaderSeed).filter(DownloaderSeed.hash == downloader_hash)
        if downloader_id is not None:
            q = q.filter(DownloaderSeed.downloader_id == downloader_id)
        seed = q.first()
        if not seed:
            return None
        snap = self._get_latest_downloader_snapshot(seed.id, session)
        return (seed, snap)

    @db_update
    def upsert_downloader_seed(
        self,
        downloader_id: str,
        hash_value: str,
        snapshot_data: dict,
        site_seed_id: Optional[int] = None,
        name: Optional[str] = None,
        snapshot_at: Optional[str] = None,
        db: Session = None,
    ) -> DownloaderSeed:
        """按下载器+hash 存在则更新、不存在则插入 DownloaderSeed，并写入一条 DownloaderSeedSnapshot；site_seed_id 一并写入。由 batch 或调用方传入 db。"""
        session = db or self._db
        snapshot_at = snapshot_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        seed = session.query(DownloaderSeed).filter(
            and_(
                DownloaderSeed.downloader_id == downloader_id,
                DownloaderSeed.hash == hash_value,
            )
        ).first()
        if not seed:
            seed = DownloaderSeed(
                downloader_id=downloader_id,
                hash=hash_value,
                site_seed_id=site_seed_id,
                name=name,
            )
            session.add(seed)
            session.flush()
        else:
            seed.site_seed_id = site_seed_id
            seed.updated_at = snapshot_at
            if name is not None:
                seed.name = name

        snap = DownloaderSeedSnapshot(
            downloader_seed_id=seed.id,
            snapshot_at=snapshot_at,
            total_size=snapshot_data.get("total_size", 0),
            ratio=snapshot_data.get("ratio", 0.0),
            state=snapshot_data.get("state"),
            tracker=snapshot_data.get("tracker"),
            extra=snapshot_data.get("extra"),
        )
        session.add(snap)
        session.flush()
        return seed

    @db_update
    def bulk_upsert_downloader_seeds(
        self,
        db: Session = None,
        rows: Optional[List[dict]] = None,
    ) -> None:
        """批量 upsert 下载器种子记录，保障 mapper 内部统一管理事务。"""
        session = db or self._db
        for row in rows or []:
            downloader_id = row.get("downloader_id") or ""
            hash_value = row.get("hash") or row.get("hash_value") or ""
            if not downloader_id or not hash_value:
                continue
            snapshot_data = row.get("snapshot_data") or {}
            self.upsert_downloader_seed(
                downloader_id=downloader_id,
                hash_value=hash_value,
                snapshot_data=snapshot_data,
                site_seed_id=row.get("site_seed_id"),
                name=row.get("name"),
                snapshot_at=row.get("snapshot_at"),
                db=session,
            )
        session.flush()

    @db_query
    def list_downloader_seeds_with_latest_snapshot(
        self,
        db: Session = None,
        downloader_ids: Optional[List[str]] = None,
        tracker_domains: Optional[List[str]] = None,
    ) -> List[Tuple[DownloaderSeed, Optional[DownloaderSeedSnapshot]]]:
        """按可选下载器 ID、可选 tracker 域名筛选，返回 DownloaderSeed 及最新快照列表。"""
        session = db or self._db
        q = session.query(DownloaderSeed)
        if downloader_ids:
            q = q.filter(DownloaderSeed.downloader_id.in_(downloader_ids))
        seeds = q.all()
        if tracker_domains:
            domain_set = set(d.lower() for d in tracker_domains)
            filtered = []
            for s in seeds:
                snap = self._get_latest_downloader_snapshot(s.id, session)
                if snap and snap.tracker:
                    t_lower = (snap.tracker or "").lower()
                    if any(d in t_lower for d in domain_set):
                        filtered.append((s, snap))
                    else:
                        continue
                else:
                    filtered.append((s, snap))
            return filtered
        return [(s, self._get_latest_downloader_snapshot(s.id, session)) for s in seeds]

    @db_update
    def batch_upsert_downloader_torrents(
        self,
        db: Session = None,
        downloader_id: str = "",
        torrent_list: Optional[List[dict]] = None,
        snapshot_at: Optional[str] = None,
    ) -> None:
        """批量 upsert DownloaderSeed 及快照；列表中每项可带 site_seed_id，保存时一并写入。"""
        session = db or self._db
        snapshot_at = snapshot_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for t in (torrent_list or []):
            h = t.get("hash") or t.get("info_hash") or ""
            if not h:
                continue
            self.upsert_downloader_seed(
                downloader_id=downloader_id,
                hash_value=h,
                snapshot_data={
                    "total_size": t.get("total_size") or t.get("size") or 0,
                    "ratio": t.get("ratio", 0.0),
                    "state": t.get("state"),
                    "tracker": t.get("tracker"),
                },
                site_seed_id=t.get("site_seed_id"),
                name=t.get("name"),
                snapshot_at=snapshot_at,
                db=session,
            )
        session.flush()

    @db_query
    def list_all_mappings(self, db: Session = None) -> List[dict]:
        """返回当前所有关联列表（site_seed_id、downloader_hash 等）。"""
        session = db or self._db
        rows = (
            session.query(DownloaderSeed.id, DownloaderSeed.site_seed_id, DownloaderSeed.hash, DownloaderSeed.downloader_id)
            .filter(DownloaderSeed.site_seed_id.isnot(None))
            .all()
        )
        return [
            {"site_seed_id": r.site_seed_id, "downloader_hash": r.hash, "downloader_id": r.downloader_id}
            for r in rows
        ]
