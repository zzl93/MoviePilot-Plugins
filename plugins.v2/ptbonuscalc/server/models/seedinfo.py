# -*- coding: utf-8 -*-
"""种子主表与每日快照子表"""
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, BigInteger, Float, DateTime, Date, ForeignKey, Index

from app.db import Base, get_id_column


class SeedInfo(Base):
    """
    种子主表：存储站点种子与下载器种子的不变信息
    网页不变：name, size, pubdate
    下载器不变：downloader_hash, downloader_tracker(原始), downloader_tracker_domain(解析后), downloader_added_at
    """
    __tablename__ = "ptbonuscalc_seedinfo"

    id = get_id_column()
    mapping_key = Column(String(512), unique=True, nullable=False, index=True)
    site_domain = Column(String(256), nullable=False, index=True)
    torrent_key = Column(String(384), nullable=False, index=True)

    # 网页不变
    name = Column(String(1024))
    size = Column(BigInteger, default=0)
    pubdate = Column(String(64))
    seed_attr = Column(String(32))  # 主辅属性：main(主)/aux(辅)

    # 下载器不变（可为空）
    downloader_hash = Column(String(64), index=True)
    downloader_tracker = Column(String(512))
    downloader_tracker_domain = Column(String(256), index=True)
    downloader_added_at = Column(DateTime)
    downloader_name = Column(String(128))
    downloader_category = Column(String(256))
    downloader_tags = Column(String(512))

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (Index("ix_ptbonuscalc_seedinfo_site_torrent", "site_domain", "torrent_key"), {"extend_existing": True})


class SeedInfoSnapshot(Base):
    """
    每日快照子表：网页/下载器可能变化的字段
    每天同一种子只保留一条，按 snapshot_date 唯一
    """
    __tablename__ = "ptbonuscalc_seedinfo_snapshot"

    id = get_id_column()
    seedinfo_id = Column(Integer, ForeignKey("ptbonuscalc_seedinfo.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_time = Column(String(32))

    # 网页状态：exists / not_exists
    web_status = Column(String(32), default="exists")
    # 下载器状态：seeding / downloading / paused / error / uploading / deleted / unknown
    downloader_status = Column(String(64), default="unknown")

    # 网页可变
    seeders = Column(Integer, default=0)
    weight = Column(Float, default=1.0)
    T_weeks = Column(Float, default=0.0)
    A_value = Column(Float, default=0.0)
    A_per_GB = Column(Float, default=0.0)
    bonus_per_hour = Column(Float, default=0.0)

    # 下载器可变
    downloader_ratio = Column(Float)
    downloader_uploaded = Column(BigInteger)
    downloader_downloaded = Column(BigInteger)
    downloader_seeding_time = Column(Integer)
    downloader_state = Column(String(64))
    downloader_save_path = Column(String(1024))
    downloader_name = Column(String(128))
    downloader_category = Column(String(256))
    downloader_tags = Column(String(512))

    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_ptbonuscalc_snapshot_seedinfo_date", "seedinfo_id", "snapshot_date", unique=True),
        {"extend_existing": True},
    )
