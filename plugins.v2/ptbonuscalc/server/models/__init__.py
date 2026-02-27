# -*- coding: utf-8 -*-
"""PT魔力计算器插件数据模型：站点种子、下载器种子（各主表+快照表）"""
from .site_seed import SiteSeed, SiteSeedSnapshot
from .downloader_seed import DownloaderSeed, DownloaderSeedSnapshot

__all__ = ["SiteSeed", "SiteSeedSnapshot", "DownloaderSeed", "DownloaderSeedSnapshot"]
