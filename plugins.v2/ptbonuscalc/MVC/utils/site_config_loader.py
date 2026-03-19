"""
按站点域名加载做种页解析配置，供 page_parser 使用。
配置位于插件 site_configs/*.json，每文件含 domains、table_selectors、column_keywords 等。
"""
import json
from pathlib import Path
from typing import Optional, Dict, Any

from app.log import logger

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "site_configs"
_CACHE: Dict[str, Dict] = {}
_CONFIG_FILE_MAP: Dict[str, Path] = {}
_DEFAULT_CONFIG: Optional[Dict] = None


def _load_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[ptbonuscalc] 加载配置 {path} 失败: {e}")
        return None


def _load_all_configs() -> None:
    global _CACHE, _CONFIG_FILE_MAP, _DEFAULT_CONFIG
    if _CACHE:
        return
    _CONFIG_FILE_MAP.clear()
    if not _CONFIG_DIR.exists():
        _DEFAULT_CONFIG = {}
        return
    for p in _CONFIG_DIR.glob("*.json"):
        cfg = _load_json(p)
        if not cfg or not isinstance(cfg, dict):
            continue
        if p.stem.lower() == "default":
            _DEFAULT_CONFIG = cfg
            continue
        for d in cfg.get("domains") or []:
            key = str(d).lower().strip()
            if key and key not in _CACHE:
                _CACHE[key] = cfg
                _CONFIG_FILE_MAP[key] = p
    if _DEFAULT_CONFIG is None:
        _DEFAULT_CONFIG = _load_json(_CONFIG_DIR / "default.json") or {}


def _domain_to_keys(domain: str) -> list:
    """从 domain 提取用于匹配的 key 列表，如 https://pt.lajidui.top/ -> ['pt.lajidui.top','lajidui.top']"""
    if not domain:
        return []
    from urllib.parse import urlparse
    try:
        if "://" not in domain:
            domain = "https://" + domain
        parsed = urlparse(domain)
        host = (parsed.netloc or parsed.path or domain).strip().lower()
    except Exception:
        host = str(domain).lower()
    if not host:
        return []
    keys = [host]
    parts = host.split(".")
    if len(parts) >= 2:
        keys.append(".".join(parts[-2:]))
    return keys


def get_site_parser_config(domain: str) -> tuple:
    """根据站点 domain 返回 (解析配置, 是否有专属配置)。无匹配时返回 (default配置, False)。"""
    _load_all_configs()
    for key in _domain_to_keys(domain):
        if key in _CACHE:
            return (dict(_CACHE[key]), True)
    d = (domain or "").lower()
    for loaded_key, cfg in _CACHE.items():
        if loaded_key in d or d.endswith(loaded_key):
            return (dict(cfg), True)
    return (dict(_DEFAULT_CONFIG or {}), False)


def save_bonus_params_for_domain(domain: str, bonus_params: dict) -> bool:
    """
    将 bonus_params 写入该 domain 对应的站点配置 json 文件。仅当 domain 有专属配置时写入。
    写入后清除缓存，使后续读取生效。返回是否成功写入。
    """
    global _CACHE, _CONFIG_FILE_MAP
    if not bonus_params or not isinstance(bonus_params, dict):
        return False
    _load_all_configs()
    for key in _domain_to_keys(domain):
        if key in _CONFIG_FILE_MAP:
            path = _CONFIG_FILE_MAP[key]
            try:
                cfg = _load_json(path) or {}
                cfg["bonus_params"] = dict(bonus_params)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                for k, p in _CONFIG_FILE_MAP.items():
                    if p == path:
                        _CACHE[k] = cfg
                logger.info(f"[ptbonuscalc] 已将 bonus_params 写入 {path.name} domain={key}")
                return True
            except Exception as e:
                logger.warning(f"[ptbonuscalc] 写入 bonus_params 到 {path} 失败: {e}")
                return False
    return False
