# -*- coding: utf-8 -*-
"""站点种子：主表与快照表"""
from datetime import datetime, date

from sqlalchemy import Column, String, Integer, BigInteger, Float, DateTime, Date, ForeignKey, Index

from app.db import Base, get_id_column


class SiteSeed(Base):
    """站点种子主表：仅站点侧不变信息"""
    __tablename__ = "ptbonuscalc_site_seed"

    id = get_id_column()
    mapping_key = Column(String(512), unique=True, nullable=False, index=True)
    site_domain = Column(String(256), nullable=False, index=True)
    torrent_id = Column(String(384), nullable=False, index=True)
    name = Column(String(1024))
    size = Column(BigInteger, default=0)
    pubdate = Column(String(64))
    seed_attr = Column(String(32))
    detail_url = Column(String(1024))
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_ptbonuscalc_site_seed_site_torrent", "site_domain", "torrent_id"),
        {"extend_existing": True},
    )


class SiteSeedSnapshot(Base):
    """站点种子快照表：站点侧每日快照"""
    __tablename__ = "ptbonuscalc_site_seed_snapshot"

    id = get_id_column()
    site_seed_id = Column(Integer, ForeignKey("ptbonuscalc_site_seed.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_time = Column(String(32))
    web_status = Column(String(32), default="exists")
    seeders = Column(Integer, default=0)
    weight = Column(Float, default=1.0)
    T_weeks = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("ix_ptbonuscalc_site_snap_seed_date", "site_seed_id", "snapshot_date", unique=True),
        {"extend_existing": True},
    )
