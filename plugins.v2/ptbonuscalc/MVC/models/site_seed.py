"""
站点种子及快照表映射，仅 ORM 定义，持久化由 Mapper 完成。
"""
from sqlalchemy import Column, Integer, String, BigInteger, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db import Base, get_id_column


class SiteSeed(Base):
    """站点种子主表：某站点下的一条做种记录"""
    __tablename__ = "ptbonuscalc_site_seed"

    id = get_id_column()
    site_id = Column(Integer, nullable=False, index=True)  # 主项目 Site.id
    torrent_id = Column(String, nullable=False, index=True)  # 站点内种子 ID
    name = Column(String)
    info_hash = Column(String, index=True)  # 可选，站点若提供则存
    created_at = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at = Column(String, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    snapshots = relationship("SiteSeedSnapshot", back_populates="site_seed", order_by="SiteSeedSnapshot.id.desc()")


class SiteSeedSnapshot(Base):
    """站点种子快照：某次同步时的做种状态（体积、做种时长、魔力等）"""
    __tablename__ = "ptbonuscalc_site_seed_snapshot"

    id = get_id_column()
    site_seed_id = Column(Integer, ForeignKey("ptbonuscalc_site_seed.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_at = Column(String, nullable=False)

    # 解析得到的字段
    size = Column(BigInteger, default=0)  # 体积 bytes
    seed_time = Column(BigInteger, default=0)  # 做种时长 秒
    bonus_per_hour = Column(Float, default=0.0)  # 每小时魔力
    bonus_params = Column(JSON)  # 站点魔力参数快照
    extra = Column(JSON)  # 其它扩展字段

    site_seed = relationship("SiteSeed", back_populates="snapshots")
