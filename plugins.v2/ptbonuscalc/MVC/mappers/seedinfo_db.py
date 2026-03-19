"""
插件四张表的建表与迁移，仅此模块动 schema。
若已有表结构与当前 Model 不一致（缺列等），先删表再重建以保证 schema 正确。
"""
from sqlalchemy import text
from app.db import Engine, ScopedSession, Base
from app.plugins.ptbonuscalc.MVC.models import (
    SiteSeed,
    SiteSeedSnapshot,
    DownloaderSeed,
    DownloaderSeedSnapshot,
)

_PLUGIN_TABLES = [
    SiteSeed.__table__,
    SiteSeedSnapshot.__table__,
    DownloaderSeed.__table__,
    DownloaderSeedSnapshot.__table__,
]

# 按依赖顺序：先删子表再删主表
_PLUGIN_TABLE_NAMES_DROP_ORDER = [
    "ptbonuscalc_downloader_seed_snapshot",
    "ptbonuscalc_downloader_seed",
    "ptbonuscalc_site_seed_snapshot",
    "ptbonuscalc_site_seed",
]


def init_seedinfo_db():
    """创建或校验四张表存在，执行迁移（schema/表结构、字段默认值等）。"""
    _drop_plugin_tables_if_schema_mismatch()
    Base.metadata.create_all(Engine, tables=_PLUGIN_TABLES, checkfirst=True)
    db = ScopedSession()
    try:
        _normalize_downloader_state(db)
    finally:
        db.close()


def _drop_plugin_tables_if_schema_mismatch():
    """若任意插件表存在但缺少当前模型要求的列，则删除四张表，由 create_all 重建。"""
    with Engine.connect() as conn:
        for table_name in _PLUGIN_TABLE_NAMES_DROP_ORDER:
            try:
                r = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"))
                if r.fetchone() is None:
                    continue
                r = conn.execute(text(f"PRAGMA table_info({table_name})"))
                cols = [row[1] for row in r.fetchall()]
            except Exception:
                continue
            required = _required_columns_for_table(table_name)
            if required and not all(c in cols for c in required):
                for t in _PLUGIN_TABLE_NAMES_DROP_ORDER:
                    try:
                        conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
                    except Exception:
                        pass
                conn.commit()
                return
    return


def _required_columns_for_table(table_name: str):
    """当前模型要求的列名列表，用于判断表是否可复用。"""
    if table_name == "ptbonuscalc_downloader_seed":
        return ["id", "downloader_id", "info_hash", "site_seed_id"]
    if table_name == "ptbonuscalc_downloader_seed_snapshot":
        return ["id", "downloader_seed_id", "snapshot_at", "total_size"]
    if table_name == "ptbonuscalc_site_seed":
        return ["id", "site_id", "torrent_id", "bonus_per_hour"]
    if table_name == "ptbonuscalc_site_seed_snapshot":
        return ["id", "site_seed_id", "snapshot_at", "size"]
    return []


def _migrate_table(db, table_name: str, migrations: list):
    """对单表执行若干迁移步骤，由 _migrate_schema 或其它迁移逻辑调用。"""
    # 预留：按表名与当前 schema 执行 DDL
    pass


def _normalize_downloader_state(db):
    """数据规范化：如 downloader 状态字段默认值等，由 init_seedinfo_db 调用。"""
    # 预留：可对已有数据做 UPDATE 规范化
    pass
