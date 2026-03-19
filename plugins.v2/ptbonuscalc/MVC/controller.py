"""
仅做接口层：接收 API 与事件，解析参数，调 Service，返回。仅调 Service，不直接调 Mapper。
"""
from typing import Any
from app.plugins.ptbonuscalc.MVC.services import (
    config_service,
    site_service,
    bonus_service,
    downloader_seed_service,
)
from app.schemas import Response


def on_site_refreshed(plugin, event) -> None:
    """Site refreshed event: sync site data and downstream downloader data."""
    site_service.trigger_sync_on_site_refresh(plugin, event)
    downloader_seed_service.sync_downloader_seeds_from_api(plugin)


def api_form_options(plugin) -> Any:
    """配置页：站点、下载器、地址关键词选项等。前端 formOptions = res，需在 res 顶层有 sites/downloaders 等。"""
    data = config_service.get_form_options(plugin)
    return {
        "success": True,
        "data": data,
        "sites": data.get("sites"),
        "downloaders": data.get("downloaders"),
        "address_keyword_options": data.get("address_keyword_options"),
        "suggested_site_mappings": data.get("suggested_site_mappings", {}),
        "sites_with_config_data": data.get("sites_with_config_data", []),
    }


def api_downloader_tracker_options(plugin, data: dict) -> Any:
    """按下载器列表返回 Tracker 地址选项。"""
    result = config_service.get_downloader_tracker_options(plugin, data or {})
    return Response(success=True, data=result)


def api_site_list(plugin, data: dict) -> Any:
    """做种页表头：已选站点及种子总数、总时魔。"""
    site_id = (data or {}).get("site_id")
    sites = site_service.get_sites_from_plugindata(plugin, site_id=site_id)
    return Response(success=True, data=sites)


def api_bonus_data(plugin, data: dict) -> Any:
    """做种明细：左表 + 右表下载器候选。"""
    data = data or {}
    payload = bonus_service.build_seed_association_payload(
        plugin,
        override_config=data.get("override_config"),
        site_id=data.get("site_id"),
        keyword=data.get("keyword"),
    )
    left_table = []
    right_table = payload.get("downloader_torrents_by_site") or {}
    for block in payload.get("blocks") or []:
        left_table.extend(block.get("rows") or [])
    return Response(
        success=True,
        data={
            "sites": payload.get("sites"),
            "left_table": left_table,
            "right_table": right_table,
            "downloader_torrents": payload.get("downloader_torrents"),
        },
    )


def api_save_data(plugin, data: dict) -> Any:
    """批量保存或取消站点种子与下载器关联。"""
    associations = (data or {}).get("associations") or []
    success, message = bonus_service.apply_save_data(plugin, associations)
    return Response(success=success, message=message)
