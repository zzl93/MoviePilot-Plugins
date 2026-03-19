"""
操作 SiteSeed / SiteSeedSnapshot，含站点层关联查询（可带 DownloaderSeed/快照）。
"""
from typing import List, Tuple, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from app.db import DbOper, db_update, db_query
from app.plugins.ptbonuscalc.MVC.models import SiteSeed, SiteSeedSnapshot, DownloaderSeed, DownloaderSeedSnapshot


class SiteSeedMapper(DbOper):
    def __init__(self, db: Session = None):
        super().__init__(db)

    @db_update
    def batch_save_seeding_from_parser(
        self,
        db: Session = None,
        site_id: int = 0,
        parser_result: Optional[dict] = None,
        snapshot_at: Optional[str] = None,
    ) -> None:
        """根据解析结果批量写入/更新站点种子及最新快照。"""
        session = db or self._db
        snapshot_at = snapshot_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        torrents = (parser_result or {}).get("torrents") or []

        for t in torrents:
            torrent_id = str(t.get("torrent_id") or "")
            if not torrent_id or not torrent_id.isdigit():
                continue
            seed = session.query(SiteSeed).filter(
                and_(SiteSeed.site_id == site_id, SiteSeed.torrent_id == torrent_id)
            ).first()
            bp = t.get("bonus_per_hour") or 0.0
            if not seed:
                seed = SiteSeed(
                    site_id=site_id,
                    torrent_id=torrent_id,
                    name=t.get("name"),
                    info_hash=t.get("info_hash"),
                    bonus_per_hour=bp,
                )
                session.add(seed)
                session.flush()
            else:
                seed.updated_at = snapshot_at
                seed.bonus_per_hour = bp
                if t.get("name") is not None:
                    seed.name = t.get("name")
                if t.get("info_hash") is not None:
                    seed.info_hash = t.get("info_hash")

            snap = SiteSeedSnapshot(
                site_seed_id=seed.id,
                snapshot_at=snapshot_at,
                size=t.get("size") or 0,
                seed_time=t.get("seed_time") or 0,
                bonus_per_hour=t.get("bonus_per_hour") or 0.0,
                extra=t.get("extra"),
            )
            session.add(snap)
        session.flush()

    @db_query
    def list_site_seeds_with_latest_snapshot_by_site(
        self, db: Session = None, site_id: int = 0
    ) -> List[Tuple[SiteSeed, SiteSeedSnapshot]]:
        """返回站点全部 SiteSeed 及其最新快照。"""
        session = db or self._db
        seeds = session.query(SiteSeed).filter(SiteSeed.site_id == site_id).all()
        out = []
        for s in seeds:
            latest = self._get_latest_site_snapshot(s.id, session)
            if latest:
                out.append((s, latest))
        return out

    @db_query
    def list_seed_with_latest_snapshot_by_site(
        self, db: Session = None, site_id: int = 0
    ) -> List[dict]:
        """返回站点种子 + 最新站点快照 + 若存在的 Downloader 关联及快照。"""
        session = db or self._db
        seeds = session.query(SiteSeed).filter(SiteSeed.site_id == site_id).all()
        result = []
        for s in seeds:
            latest_snap = self._get_latest_site_snapshot(s.id, session)
            down_seed = session.query(DownloaderSeed).filter(
                DownloaderSeed.site_seed_id == s.id
            ).first()
            down_snap = None
            if down_seed:
                down_snap = session.query(DownloaderSeedSnapshot).filter(
                    DownloaderSeedSnapshot.downloader_seed_id == down_seed.id
                ).order_by(desc(DownloaderSeedSnapshot.id)).first()
            result.append({
                "site_seed": s,
                "site_snapshot": latest_snap,
                "downloader_seed": down_seed,
                "downloader_snapshot": down_snap,
            })
        return result

    @db_query
    def get_seed_with_latest_snapshot(self, db: Session = None, site_seed_id: int = 0) -> Optional[dict]:
        """单条站点种子 + 最新站点快照 + 若存在的 Downloader 关联及快照。"""
        session = db or self._db
        seed = session.query(SiteSeed).filter(SiteSeed.id == site_seed_id).first()
        if not seed:
            return None
        latest_snap = self._get_latest_site_snapshot(site_seed_id, session)
        down_seed = session.query(DownloaderSeed).filter(
            DownloaderSeed.site_seed_id == site_seed_id
        ).first()
        down_snap = None
        if down_seed:
            down_snap = session.query(DownloaderSeedSnapshot).filter(
                DownloaderSeedSnapshot.downloader_seed_id == down_seed.id
            ).order_by(desc(DownloaderSeedSnapshot.id)).first()
        return {
            "site_seed": seed,
            "site_snapshot": latest_snap,
            "downloader_seed": down_seed,
            "downloader_snapshot": down_snap,
        }

    @db_query
    def get_site_seed(self, db: Session = None, site_seed_id: int = 0) -> Optional[SiteSeed]:
        """按主键获取单条 SiteSeed。"""
        session = db or self._db
        return session.query(SiteSeed).filter(SiteSeed.id == site_seed_id).first()

    def _get_latest_site_snapshot(self, site_seed_id: int, db: Session) -> Optional[SiteSeedSnapshot]:
        """查询站点种子的最新快照（内部辅助）。"""
        return (
            db.query(SiteSeedSnapshot)
            .filter(SiteSeedSnapshot.site_seed_id == site_seed_id)
            .order_by(desc(SiteSeedSnapshot.id))
            .first()
        )

    @db_update
    def save_seed_info(
        self,
        db: Session = None,
        site_id: int = 0,
        torrent_id: str = "",
        snapshot_data: Optional[dict] = None,
        name: Optional[str] = None,
        info_hash: Optional[str] = None,
        snapshot_at: Optional[str] = None,
    ) -> SiteSeed:
        """写入/更新单条 SiteSeed 及对应快照。"""
        session = db or self._db
        snapshot_at = snapshot_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        seed = session.query(SiteSeed).filter(
            and_(SiteSeed.site_id == site_id, SiteSeed.torrent_id == torrent_id)
        ).first()
        bp = (snapshot_data or {}).get("bonus_per_hour", 0.0)
        if not seed:
            seed = SiteSeed(
                site_id=site_id,
                torrent_id=torrent_id,
                name=name,
                info_hash=info_hash,
                bonus_per_hour=bp,
            )
            session.add(seed)
            session.flush()
        else:
            seed.bonus_per_hour = bp
            if name is not None:
                seed.name = name
            if info_hash is not None:
                seed.info_hash = info_hash
            seed.updated_at = snapshot_at

        snapshot_data = snapshot_data or {}
        snap = SiteSeedSnapshot(
            site_seed_id=seed.id,
            snapshot_at=snapshot_at,
            size=snapshot_data.get("size", 0),
            seed_time=snapshot_data.get("seed_time", 0),
            bonus_per_hour=snapshot_data.get("bonus_per_hour", 0.0),
            extra=snapshot_data.get("extra"),
        )
        session.add(snap)
        session.flush()
        return seed
