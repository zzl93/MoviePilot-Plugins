"""
插件与 DB 初始化。
"""
from app.plugins.ptbonuscalc.MVC.mappers.seedinfo_db import init_seedinfo_db


def init_plugin_db(plugin) -> None:
    """建表及迁移，供 __init__.init_plugin 首先调用。"""
    init_seedinfo_db()
