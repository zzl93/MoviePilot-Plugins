# -*- coding: utf-8 -*-
"""
我的刷流插件：暂时仅实现站点信息查询与种子查询，结果通过 DEBUG 日志输出。
阶段1 含：插件配置(1.1～1.3)、每个种子的精细化存储(2.1～2.4)。同步数据下载器下全部种子（所有状态）入库、去重，is_brush 标识刷流任务。
"""
import json
from typing import Any, List, Dict, Tuple, Optional

from apscheduler.triggers.cron import CronTrigger
from fastapi import Query
from app.chain.torrents import TorrentsChain
from app.chain.download import DownloadChain
from app.core.module import ModuleManager
from app.helper.service import ServiceConfigHelper
from app.helper.sites import SitesHelper
from app.helper.torrent import TorrentHelper
from app.helper.directory import DirectoryHelper
from app.db.site_oper import SiteOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import DownloaderType

# 种子存储 key（save_data），存同步数据下载器下全部种子（所有状态），用 is_brush 区分是否刷流任务
BRUSH_TORRENTS_KEY = "brush_torrents"


def _cfg(config: Optional[dict], key: str, default: Any = None) -> Any:
    """从插件配置中读取项，兼容带 mybrush_ 前缀的键名。"""
    if not config:
        return default
    v = config.get(key)
    if v is not None:
        return v
    return config.get(f"mybrush_{key}", default)


class MyBrush(_PluginBase):
    plugin_name = "我的刷流"
    plugin_desc = "刷流插件，暂支持站点信息查询与种子查询，结果输出到 DEBUG 日志。"
    plugin_icon = "https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/brushmanager.png"
    plugin_version = "0.1"
    plugin_author = "user"
    plugin_config_prefix = "mybrush_"
    plugin_order = 50
    auth_level = 1

    site_oper = None
    sites_helper = None
    torrents_chain = None
    download_chain = None
    torrent_helper = None
    directory_helper = None

    # 阶段1 配置（在 init_plugin 中从 config 读取）
    enabled: bool = False
    onlyonce: bool = False
    cron: str = ""
    brush_sites: List[str] = []
    brush_dir: str = ""
    sync_downloaders: List[str] = []
    brush_tag: str = "brush"
    downloader: str = ""
    max_tasks: int = 0
    torrent_source: str = "browse"
    select_volume_factor: Optional[float] = None
    size_min: Optional[int] = None
    sync_interval: int = 0

    def init_plugin(self, config: dict = None):
        config = config or {}
        self.site_oper = SiteOper()
        self.sites_helper = SitesHelper()
        self.torrents_chain = TorrentsChain()
        self.download_chain = DownloadChain()
        self.torrent_helper = TorrentHelper()
        self.directory_helper = DirectoryHelper()

        self.enabled = bool(_cfg(config, "enabled"))
        self.onlyonce = bool(_cfg(config, "onlyonce"))
        self.cron = _cfg(config, "cron") or ""
        self.brush_sites = _cfg(config, "brush_sites") or []
        if isinstance(self.brush_sites, str):
            self.brush_sites = [s.strip() for s in self.brush_sites.split(",") if s.strip()]
        self.brush_dir = _cfg(config, "brush_dir") or ""
        self.sync_downloaders = _cfg(config, "sync_downloaders") or []
        if isinstance(self.sync_downloaders, str):
            self.sync_downloaders = [s.strip() for s in self.sync_downloaders.split(",") if s.strip()]
        self.brush_tag = (_cfg(config, "brush_tag") or "brush").strip() or "brush"
        self.downloader = _cfg(config, "downloader") or ""
        self.max_tasks = int(_cfg(config, "max_tasks") or 0)
        self.torrent_source = _cfg(config, "torrent_source") or "browse"
        self.select_volume_factor = _cfg(config, "select_volume_factor")
        self.size_min = _cfg(config, "size_min")
        self.sync_interval = int(_cfg(config, "sync_interval") or 0)
        # 2.1 存储：确保有 brush_torrents 结构（dict: hash -> 种子详情）
        if self.get_data(BRUSH_TORRENTS_KEY) is None:
            self.save_data(BRUSH_TORRENTS_KEY, {})

        # 立即运行一次：保存配置后若 onlyonce 为 true，执行一次 QB→存储 同步并清除 onlyonce
        if self.onlyonce:
            logger.info("[MyBrush] 立即运行一次：执行 QB→存储 同步")
            self._sync_qb_to_storage()
            config["onlyonce"] = False
            self.update_config(config)

    def _get_brush_storage(self) -> Dict[str, dict]:
        """读取刷流种子存储，返回 { hash: {...} }"""
        data = self.get_data(BRUSH_TORRENTS_KEY)
        return data if isinstance(data, dict) else {}

    def _save_brush_storage(self, data: Dict[str, dict]) -> None:
        """写入刷流种子存储"""
        self.save_data(BRUSH_TORRENTS_KEY, data)

    def _has_brush_tag(self, torrent_tags: Optional[str]) -> bool:
        """2.2 识别是否为刷流任务：种子 tags 包含配置的 brush_tag。"""
        tag = (self.brush_tag or "").strip()
        if not tag:
            return False
        tags_set = {t.strip() for t in (torrent_tags or "").split(",") if t.strip()}
        return tag in tags_set

    def _sync_qb_to_storage(self) -> Tuple[int, int]:
        """2.3 从配置的同步下载器拉取全部种子（所有状态）并落库；is_brush 表示是否带刷流标签。返回 (成功数, 失败数)。"""
        downloader_names = list(self.sync_downloaders) if self.sync_downloaders else ([self.downloader] if self.downloader else [])
        if not downloader_names:
            logger.warning("[MyBrush] 未配置同步数据下载器，跳过同步")
            return 0, 0
        qb_module = None
        for m in ModuleManager().get_running_subtype_module(DownloaderType.Qbittorrent):
            qb_module = m
            break
        if not qb_module:
            logger.warning("[MyBrush] 未获取到 Qbittorrent 模块，跳过同步")
            return 0, 0
        storage = self._get_brush_storage()
        ok, fail = 0, 0
        seen_hashes = set()  # 用于去重：记录已处理的 hash
        for name in downloader_names:
            qb = qb_module.get_instance(name)
            if not qb:
                continue
            torrents, err = qb.get_torrents()
            if err:
                logger.warning("[MyBrush] 拉取种子列表异常 downloader=%s", name)
                continue
            for t in torrents or []:
                hash_str = t.get("hash")
                if not hash_str:
                    fail += 1
                    continue
                # 去重：同一个 hash 只入库一次（以第一次遇到的下载器为准）
                if hash_str in seen_hashes:
                    continue
                seen_hashes.add(hash_str)
                tags = t.get("tags") or ""
                state = t.get("state") or ""
                try:
                    storage[hash_str] = {
                        "hash": hash_str,
                        "downloader": name,
                        "site_domain": "",
                        "site_name": "",
                        "added_at": storage.get(hash_str, {}).get("added_at") or t.get("added_on") or 0,
                        "ratio": float(t.get("ratio") or 0),
                        "uploaded": float(t.get("uploaded") or 0),
                        "downloaded": float(t.get("downloaded") or 0),
                        "seeding_time": int(t.get("seeding_time") or 0),
                        "name": t.get("name") or "",
                        "total_size": int(t.get("total_size") or 0),
                        "state": state,
                        "is_brush": self._has_brush_tag(tags),
                    }
                    ok += 1
                except Exception as e:
                    logger.debug("[MyBrush] 落库单条失败 %s: %s", hash_str, e)
                    fail += 1
        self._save_brush_storage(storage)
        logger.info("[MyBrush] QB→存储 同步完成: 更新 %s 条, 失败 %s 条", ok, fail)
        return ok, fail

    def get_state(self) -> bool:
        return self.enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/sites",
                "endpoint": self._api_sites,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "站点信息查询",
                "description": "查询站点列表并通过 DEBUG 日志输出",
            },
            {
                "path": "/torrents",
                "endpoint": self._api_torrents,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "种子查询",
                "description": "按站点查询种子并通过 DEBUG 日志输出",
            },
        ]

    def _api_sites(self, request=None):
        """站点信息查询：获取站点列表并用 logger.debug 输出。"""
        indexers = self.sites_helper.get_indexers()
        if not indexers:
            logger.debug("[MyBrush] 站点信息查询：未获取到任何站点")
            return {"count": 0, "message": "无站点，已输出到 DEBUG 日志"}

        for site_item in indexers:
            site_id = site_item.get("id")
            siteinfo = self.site_oper.get(site_id) if site_id else None
            if siteinfo:
                logger.debug(f"[MyBrush] 站点信息：{json.dumps(siteinfo.to_dict(), ensure_ascii=False, default=str)}")
            else:
                logger.debug(f"[MyBrush] 站点项（无详情）：{site_item}")

        return {"count": len(indexers), "message": "站点信息已输出到 DEBUG 日志"}

    def _api_torrents(self, site_id: Optional[str] = Query(None)):
        """种子查询：按站点获取种子列表并用 logger.debug 输出。GET 参数 site_id 为站点 ID。"""
        if not site_id:
            return {"count": 0, "message": "缺少参数 site_id"}

        siteinfo = self.site_oper.get(site_id)
        if not siteinfo:
            logger.debug(f"[MyBrush] 种子查询：站点不存在 site_id={site_id}")
            return {"count": 0, "message": "站点不存在"}

        torrents = self.torrents_chain.browse(domain=siteinfo.domain)
        if not torrents:
            logger.debug(f"[MyBrush] 种子查询：站点 {siteinfo.name} 无种子")
            return {"count": 0, "message": "已输出到 DEBUG 日志"}

        for t in torrents:
            if t.downloadvolumefactor == 0:
                logger.debug(f"[MyBrush] 种子信息1：{t}")

        return {"count": len(torrents), "message": "种子信息已输出到 DEBUG 日志"}

    def get_qb_instance(self, downloader_name: Optional[str] = None):
        """按配置的下载器名获取 Qbittorrent 实例，供后续拉取全部种子数据使用。非 QB 或未配置时返回 None。"""
        name = downloader_name or self.downloader
        if not name:
            return None
        for module in ModuleManager().get_running_subtype_module(DownloaderType.Qbittorrent):
            server = module.get_instance(name)
            if server is not None:
                return server
        return None

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        indexers = SitesHelper().get_indexers()
        site_options = [{"title": (s.get("name") or s.get("id") or ""), "value": s.get("id")} for s in (indexers or []) if s.get("id")]

        downloader_confs = ServiceConfigHelper.get_downloader_configs()
        downloader_options = [{"title": c.name or "", "value": c.name} for c in (downloader_confs or []) if c.name and c.enabled]

        dirs = DirectoryHelper().get_download_dirs()
        dir_options = [{"title": f"{d.name or d.download_path or ''} ({d.download_path})", "value": (d.download_path or "")} for d in (dirs or [])]
        if not dir_options:
            dir_options = [{"title": "请先在系统配置中添加下载目录", "value": ""}]

        conf = [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                            "hint": "开启后插件将处于激活状态",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                            "hint": "保存后立即执行一次刷流任务",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "5位cron表达式",
                                            "hint": "如 0 */30 * * * 表示每30分钟",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "brush_sites",
                                            "label": "刷流站点",
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "items": site_options,
                                            "hint": "多选参与刷流的站点",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "brush_dir",
                                            "label": "刷流下载目录",
                                            "items": dir_options,
                                            "hint": "选择或填写下载目录",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "sync_downloaders",
                                            "label": "同步数据下载器",
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "items": downloader_options,
                                            "hint": "多选，从这些下载器拉取全部种子（所有状态）并去重入库",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "brush_tag",
                                            "label": "刷流标签",
                                            "placeholder": "brush",
                                            "hint": "带此标签的种子视为刷流任务，添加任务时打此标签",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "downloader",
                                            "label": "使用的下载器",
                                            "items": downloader_options,
                                            "hint": "选种加种时使用的下载器",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "torrent_source",
                                            "label": "种子来源",
                                            "items": [
                                                {"title": "首页", "value": "browse"},
                                                {"title": "RSS", "value": "rss"},
                                            ],
                                            "hint": "选种来源",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_tasks",
                                            "label": "最大同时任务数",
                                            "type": "number",
                                            "hint": "同时进行刷流的最大任务数",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "select_volume_factor",
                                            "label": "选种门槛（下载系数）",
                                            "items": [
                                                {"title": "不限制", "value": ""},
                                                {"title": "仅免费", "value": "0"},
                                                {"title": "2x 及以下", "value": "0.5"},
                                            ],
                                            "hint": "基础选种门槛",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "size_min",
                                            "label": "体积下限（GB）",
                                            "type": "number",
                                            "placeholder": "留空不限制",
                                            "hint": "可选，最小体积",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "sync_interval",
                                            "label": "与站点同步间隔（分钟）",
                                            "type": "number",
                                            "hint": "与 PT 站账户数据同步间隔，可选",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ]
        model = {
            "enabled": False,
            "onlyonce": False,
            "cron": "",
            "brush_sites": [],
            "brush_dir": "",
            "sync_downloaders": [],
            "brush_tag": "brush",
            "downloader": "",
            "torrent_source": "browse",
            "max_tasks": 5,
            "select_volume_factor": "",
            "size_min": "",
            "sync_interval": 15,
        }
        return conf, model

    def get_page(self):
        return None

    def get_service(self) -> List[Dict[str, Any]]:
        """2.4 定时任务：仅在 enabled 时执行；周期优先用配置的 cron，否则默认每 15 分钟。"""
        services = []
        if not self.enabled:
            return services
        cron = (self.cron or "").strip()
        if not cron:
            cron = "*/15 * * * *"
        services.append({
            "id": "MyBrushSyncQB",
            "name": "种子数据同步",
            "trigger": CronTrigger.from_crontab(cron),
            "func": self._sync_qb_to_storage,
            "kwargs": {},
        })
        return services

    def stop_service(self):
        pass