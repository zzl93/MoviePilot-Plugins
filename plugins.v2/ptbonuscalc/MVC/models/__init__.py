# 插件四张表映射，持久化仅经 Mapper
from .site_seed import SiteSeed, SiteSeedSnapshot
from .downloader_seed import DownloaderSeed, DownloaderSeedSnapshot

__all__ = [
    "SiteSeed",
    "SiteSeedSnapshot",
    "DownloaderSeed",
    "DownloaderSeedSnapshot",
]
