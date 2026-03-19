import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.plugins.ptbonuscalc.MVC.utils.page_parser import parse_torrent_activity_nexusphp
from app.plugins.ptbonuscalc.MVC.utils.bonus import calc_bonus_per_hour

PLUGIN_ROOT = PROJECT_ROOT / "app" / "plugins" / "ptbonuscalc"


def test_parse_torrent_activity_extracts_seed_time_and_size():
    html_path = PLUGIN_ROOT / "temp" / "lajidui-getusertorrentlistajax.html"
    html_text = html_path.read_text(encoding="utf-8")
    result = parse_torrent_activity_nexusphp(html_text)
    torrents = result["torrents"]
    assert torrents, "parser should return at least one torrent row"
    first = torrents[0]
    assert first["size"] > 0
    assert first["seed_time"] > 0
    assert isinstance(first["extra"].get("seeders"), int)


def test_calc_bonus_per_hour_matches_reference_formula():
    params = {"T0": 28, "N0": 18, "B0": 1, "L": 35, "hourly_bonus_decimals": 3}
    size_bytes = 20 * 1024 ** 3
    seed_time_seconds = 14 * 24 * 3600
    seeders = 5

    computed = calc_bonus_per_hour(
        size_bytes=size_bytes,
        seed_time_seconds=seed_time_seconds,
        bonus_params=params,
        seeders=seeders,
        weight=1.0,
    )

    T_weeks = seed_time_seconds / (7 * 24 * 3600)
    c1 = 1.0 - math.pow(10, -T_weeks / params["T0"])
    denom = max(1, params["N0"] - 1)
    c2 = 1.0 + math.sqrt(2) * math.pow(10, -(seeders - 1) / denom)
    size_gb = size_bytes / (1024 ** 3)
    A = c1 * size_gb * c2
    expected = params["B0"] * (2.0 / math.pi) * math.atan(A / params["L"])
    expected = round(expected, params["hourly_bonus_decimals"])

    assert math.isclose(computed, expected)
