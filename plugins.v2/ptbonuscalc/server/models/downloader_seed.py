# -*- coding: utf-8 -*-
"""下载器种子：主表与快照表"""
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, BigInteger, Float, DateTime, Date, ForeignKey, Index

from app.db import Base, get_id_column


class DownloaderSeed(Base):
    """下载器种子主表：仅下载器侧不变信息，site_seed_id 表示关联的站点种子"""
    __tablename__ = "ptbonuscalc_downloader_seed"

    id = get_id_column()
    downloader_hash = Column(String(64), nullable=False, index=True)
    downloader_name = Column(String(128), nullable=False, index=True)
    downloader_torrent_name = Column(String(1024))
    size = Column(BigInteger, default=0)
    tracker = Column(String(512))
    tracker_domain = Column(String(256), index=True)
    added_at = Column(DateTime)
    site_seed_id = Column(Integer, ForeignKey("ptbonuscalc_site_seed.id", ondelete="SET NULL"), index=True)
    seed_attr = Column(String(32))
    main_hash = Column(String(64))
    main_tracker_domain = Column(String(256))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_ptbonuscalc_dl_seed_domain_hash", "tracker_domain", "downloader_hash", unique=True),
        {"extend_existing": True},
    )


class DownloaderSeedSnapshot(Base):
    """下载器种子快照表：下载器侧每日快照"""
    __tablename__ = "ptbonuscalc_downloader_seed_snapshot"

    id = get_id_column()
    downloader_seed_id = Column(Integer, ForeignKey("ptbonuscalc_downloader_seed.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_time = Column(String(32))
    downloader_status = Column(String(64), default="unknown")
    ratio = Column(Float)
    uploaded = Column(BigInteger)
    downloaded = Column(BigInteger)
    seeding_time = Column(Integer)
    save_path = Column(String(1024))
    category = Column(String(256))
    tags = Column(String(512))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_ptbonuscalc_dl_snap_seed_date", "downloader_seed_id", "snapshot_date", unique=True),
        {"extend_existing": True},
    )
