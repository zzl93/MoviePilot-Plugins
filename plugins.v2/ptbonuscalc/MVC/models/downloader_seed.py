"""
下载器种子及快照表映射，仅 ORM 定义，持久化由 Mapper 完成。
关联即 site_seed_id：非空表示已关联站点种子，空表示未关联。
"""
from sqlalchemy import Column, Integer, String, BigInteger, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db import Base, get_id_column


class DownloaderSeed(Base):
    """下载器种子主表：某下载器中的一条种子，可关联到站点种子"""
    __tablename__ = "ptbonuscalc_downloader_seed"

    id = get_id_column()
    downloader_id = Column(String, nullable=False, index=True)  # 下载器标识，与主项目一致
    hash = Column("info_hash", String, nullable=False, index=True)  # 库列名 info_hash，避免保留字
    site_seed_id = Column(Integer, ForeignKey("ptbonuscalc_site_seed.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String)
    created_at = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    snapshots = relationship(
        "DownloaderSeedSnapshot",
        back_populates="downloader_seed",
        order_by="DownloaderSeedSnapshot.id.desc()",
    )


class DownloaderSeedSnapshot(Base):
    """下载器种子快照：某次拉取时的状态（体积、分享率、tracker 等）"""
    __tablename__ = "ptbonuscalc_downloader_seed_snapshot"

    id = get_id_column()
    downloader_seed_id = Column(
        Integer,
        ForeignKey("ptbonuscalc_downloader_seed.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_at = Column(String, nullable=False)

    total_size = Column(BigInteger, default=0)
    ratio = Column(Float, default=0.0)
    state = Column(String)  # 做种/下载等状态
    tracker = Column(String)  # tracker 地址
    extra = Column(JSON)

    downloader_seed = relationship("DownloaderSeed", back_populates="snapshots")
