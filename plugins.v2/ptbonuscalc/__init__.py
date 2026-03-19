# -*- coding: utf-8 -*-
from typing import Any, List, Dict, Optional, Tuple
from fastapi import Body
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.schemas import Response


class PTBonusCalc(_PluginBase):
    plugin_name = "PT 魔力/做种计算"
    plugin_desc = "本地种子管理、站点做种与下载器关联、PT 魔力计算"
    plugin_version = "0.1.0"

    def init_plugin(self, config: dict = None):
        from app.log import logger
        config = config or {}
        logger.info(f"[ptbonuscalc] init_plugin 调用 config_keys={list(config.keys())} selected_sites={config.get('selected_sites')}")
        # 兼容 Vue 表单的 primary_downloaders + aux_downloaders，合并为 sync_downloaders
        legacy = config.get("sync_downloaders") or []
        primary = config.get("primary_downloaders") or []
        aux = config.get("aux_downloaders") or []
        self.sync_downloaders = primary + aux if (primary or aux) else legacy
        self.selected_sites = config.get("selected_sites") or []
        # 兼容 site_address_mapping_{domain} 结构
        mappings = config.get("site_address_mappings") or {}
        if isinstance(mappings, dict):
            self.site_address_mappings = dict(mappings)
        else:
            self.site_address_mappings = {}
        for k, v in (config or {}).items():
            if isinstance(k, str) and k.startswith("site_address_mapping_") and v is not None:
                domain = k.replace("site_address_mapping_", "", 1)
                self.site_address_mappings[domain] = v if isinstance(v, list) else ([v] if v else [])
        if not getattr(self, "_site_refresh_listener_added", False):
            # 必须传 self._on_site_refreshed 而非 lambda：事件系统通过 method_name 从 plugin 取方法，lambda 的 method_name 为 "<lambda>" 会取不到
            self.eventmanager.add_event_listener(
                EventType.SiteRefreshed,
                self._on_site_refreshed,
            )
            self._site_refresh_listener_added = True
            logger.info("[ptbonuscalc] 已注册 SiteRefreshed 事件监听")
        from app.plugins.ptbonuscalc.MVC.services import init_service, site_service, bonus_service
        try:
            init_service.init_plugin_db(self)
            site_service.write_site_infos_for_selected(self)
            bonus_service.sync_init_mappings_and_fully_matched(self, config)
        except Exception as e:
            import traceback
            from app.log import logger
            logger.warning(f"[ptbonuscalc] init_plugin 部分逻辑失败（插件仍可加载）: {e}\n{traceback.format_exc()}")

    def _on_site_refreshed(self, event):
        from app.log import logger
        payload = getattr(event, "event_data", None) or getattr(event, "data", None)
        logger.info(f"[ptbonuscalc] 收到 SiteRefreshed 事件 payload={payload}")
        from app.plugins.ptbonuscalc.MVC.controller import on_site_refreshed
        on_site_refreshed(self, event)

    def get_state(self) -> bool:
        """始终返回 True，确保配置页和做种页可展示；有 sync_downloaders 或 selected_sites 时功能可用。"""
        return True

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/trigger_site_sync", "endpoint": self._api_trigger_site_sync, "methods": ["POST"], "auth": "bear", "summary": "手动触发站点同步", "description": "调试用：模拟 SiteRefreshed，POST {site_id: 1} 或 {site_id: \"*\"}"},
            {"path": "/form_options", "endpoint": self._api_form_options, "methods": ["GET"], "auth": "bear", "summary": "表单选项", "description": "配置页站点、下载器、地址关键词选项"},
            {"path": "/downloader_tracker_options", "endpoint": self._api_downloader_tracker_options, "methods": ["POST"], "auth": "bear", "summary": "下载器 Tracker 选项", "description": "按下载器列表返回 Tracker 地址选项"},
            {"path": "/site_list", "endpoint": self._api_site_list, "methods": ["POST"], "auth": "bear", "summary": "站点列表", "description": "做种页表头已选站点及种子总数、总时魔"},
            {"path": "/bonus_data", "endpoint": self._api_bonus_data, "methods": ["POST"], "auth": "bear", "summary": "做种明细", "description": "左表+右表下载器候选"},
            {"path": "/save_data", "endpoint": self._api_save_data, "methods": ["POST"], "auth": "bear", "summary": "保存关联", "description": "批量保存或取消站点种子与下载器关联"},
        ]

    def _api_trigger_site_sync(self, data: dict = Body(default={})):
        """调试：手动触发站点做种数据同步，等同收到 SiteRefreshed 时的逻辑。"""
        from app.log import logger
        from types import SimpleNamespace
        site_id = (data or {}).get("site_id")
        logger.info(f"[ptbonuscalc] 手动触发 trigger_site_sync site_id={site_id}")
        ev = SimpleNamespace(event_data={"site_id": site_id}, data={"site_id": site_id})
        self._on_site_refreshed(ev)
        return Response(success=True, message=f"已触发同步 site_id={site_id}")

    def _api_form_options(self):
        from app.plugins.ptbonuscalc.MVC.controller import api_form_options
        return api_form_options(self)

    def _api_downloader_tracker_options(self, data: dict = Body(default={})):
        from app.plugins.ptbonuscalc.MVC.controller import api_downloader_tracker_options
        return api_downloader_tracker_options(self, data or {})

    def _api_site_list(self, data: dict = Body(default={})):
        from app.plugins.ptbonuscalc.MVC.controller import api_site_list
        return api_site_list(self, data or {})

    def _api_bonus_data(self, data: dict = Body(default={})):
        from app.plugins.ptbonuscalc.MVC.controller import api_bonus_data
        return api_bonus_data(self, data or {})

    def _api_save_data(self, data: dict = Body(default={})):
        from app.plugins.ptbonuscalc.MVC.controller import api_save_data
        return api_save_data(self, data or {})

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        return "vue", "dist/assets"

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        return None, {
            "sync_downloaders": [],
            "selected_sites": [],
            "site_address_mappings": {},
            "primary_downloaders": [],
            "aux_downloaders": [],
        }

    def get_page(self) -> Optional[List[dict]]:
        return None

    def stop_service(self):
        pass
