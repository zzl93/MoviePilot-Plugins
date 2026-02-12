# -*- coding: utf-8 -*-
"""
PT魔力计算器插件测试：显示站点有值时 page、站点地址映射、种子关联管理才有对应的站点数据。
"""
import unittest
from unittest.mock import patch, MagicMock

try:
    from app.plugins.ptbonuscalc import PTBonusCalc
except ImportError:
    PTBonusCalc = None


def _mock_site_block(domain: str, site_name: str = None, has_unmatched: bool = True):
    """构造模拟站点数据块"""
    torrent = {
        "torrent_id": "1",
        "name": "test.torrent",
        "size": 1024,
        "matched": False,
    } if has_unmatched else {"torrent_id": "1", "name": "test.torrent", "size": 1024, "matched": True}
    return {
        "domain": domain,
        "site_name": site_name or domain,
        "torrents": [torrent] if has_unmatched else [],
    }


@unittest.skipIf(PTBonusCalc is None, "ptbonuscalc 插件未安装")
class TestPTBonusCalcSelectedSites(unittest.TestCase):
    """显示站点有值时 page、站点地址映射、种子关联管理才有对应站点数据"""

    def test_get_page_without_selected_sites_returns_config_prompt(self):
        """显示站点未配置时，get_page 返回配置提示，不展示站点"""
        plugin = PTBonusCalc()
        plugin.selected_sites = []
        result = plugin.get_page()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        self.assertIn("请先在插件配置中勾选", result[0].get("text", ""))
        self.assertIn("显示站点", result[0].get("text", ""))

    @patch.object(PTBonusCalc, "_get_bonus_seeding_data", return_value=[])
    def test_get_page_with_selected_sites_shows_data_or_empty(self, _mock):
        """显示站点已配置时，get_page 不返回「请先配置」提示"""
        plugin = PTBonusCalc()
        plugin.selected_sites = ["example.com"]
        result = plugin.get_page()
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        self.assertNotIn("请先在插件配置中勾选「显示站点」", result[0].get("text", ""))

    @patch.object(PTBonusCalc, "_get_bonus_seeding_data")
    def test_build_seed_association_payload_without_selected_sites_returns_empty_unmatched(self, mock_data):
        """显示站点未配置时，种子关联管理 unmatched_by_site 为空"""
        mock_data.return_value = [
            _mock_site_block("a.com", has_unmatched=True),
            _mock_site_block("b.com", has_unmatched=True),
        ]
        plugin = PTBonusCalc()
        plugin.selected_sites = []
        plugin.sites_helper = MagicMock()
        plugin.sites_helper.get_indexers.return_value = []
        plugin.sync_downloaders = []
        plugin.get_data = MagicMock(return_value={})

        payload = plugin._build_seed_association_payload()

        self.assertEqual(payload["unmatched_by_site"], {})
        self.assertEqual(payload["total_unmatched_count"], 0)

    @patch.object(PTBonusCalc, "_get_bonus_seeding_data")
    def test_build_seed_association_payload_with_selected_sites_filters_by_sites(self, mock_data):
        """显示站点已配置时，种子关联管理只展示已选站点的未匹配数据"""
        mock_data.return_value = [
            _mock_site_block("a.com", has_unmatched=True),
            _mock_site_block("b.com", has_unmatched=True),
        ]
        plugin = PTBonusCalc()
        plugin.selected_sites = ["a.com"]
        plugin.sites_helper = MagicMock()
        plugin.sites_helper.get_indexers.return_value = []
        plugin.sync_downloaders = []
        plugin.get_data = MagicMock(return_value={})

        payload = plugin._build_seed_association_payload()

        self.assertIn("a.com", payload["unmatched_by_site"])
        self.assertNotIn("b.com", payload["unmatched_by_site"])
        self.assertEqual(payload["total_unmatched_count"], 1)

    @patch.object(PTBonusCalc, "_fetch_downloader_torrents", return_value={})
    @patch.object(PTBonusCalc, "_get_bonus_seeding_data")
    def test_get_form_without_selected_sites_has_no_site_mapping_or_seed_panels(self, mock_data, _mock_fetch):
        """显示站点未配置时，表单弹窗中站点地址映射与种子关联管理无站点数据"""
        mock_data.return_value = [_mock_site_block("example.com", has_unmatched=True)]
        mock_sites = [
            {"name": "Example", "domain": "https://example.com/", "is_active": True},
        ]
        with patch("app.plugins.ptbonuscalc.ServiceConfigHelper.get_downloader_configs", return_value=[]):
            with patch("app.plugins.ptbonuscalc.SitesHelper") as MockSites:
                MockSites.return_value.get_indexers.return_value = mock_sites
                plugin = PTBonusCalc()
                plugin.selected_sites = []
                plugin.sites_helper = MockSites.return_value
                plugin.sync_downloaders = []
                plugin.get_data = MagicMock(return_value={})

                form_items, _ = plugin.get_form()

        vdialog = form_items[0]["content"][-1]
        self.assertEqual(vdialog["component"], "VDialog")
        vcard_text = vdialog["content"][0]["content"][1]
        dialog_body = vcard_text["content"]
        # 站点地址映射区域：selected_sites 为空时 sites_for_mapping 为空，无 VRow
        site_mapping_vrows = [c for c in dialog_body if c.get("component") == "VRow"]
        # 种子关联管理：unmatched_by_site 为空时无 VExpansionPanels 区块
        body_str = str(dialog_body)
        self.assertEqual(len(site_mapping_vrows), 0)
        self.assertIn("分布在 0 个站点", body_str)

    def _find_in_content(self, items):
        """递归收集所有子节点"""
        out = []
        for item in (items if isinstance(items, list) else [items]):
            if not isinstance(item, dict):
                continue
            out.append(item)
            for k in ("content",):
                if k in item and item[k]:
                    sub = item[k] if isinstance(item[k], list) else [item[k]]
                    out.extend(self._find_in_content(sub))
        return out

    @patch.object(PTBonusCalc, "_fetch_downloader_torrents", return_value={})
    @patch.object(PTBonusCalc, "_get_bonus_seeding_data")
    def test_get_form_with_selected_sites_has_site_data(self, mock_data, _mock_fetch):
        """显示站点已配置且该站点有数据时，表单弹窗中有站点地址映射和种子关联数据"""
        mock_data.return_value = [_mock_site_block("example.com", has_unmatched=True)]
        mock_sites = [
            {"name": "Example", "domain": "https://example.com/", "is_active": True},
        ]
        with patch("app.plugins.ptbonuscalc.ServiceConfigHelper.get_downloader_configs", return_value=[]):
            with patch("app.plugins.ptbonuscalc.SitesHelper") as MockSites:
                MockSites.return_value.get_indexers.return_value = mock_sites
                plugin = PTBonusCalc()
                plugin.selected_sites = ["example.com"]
                plugin.sites_helper = MockSites.return_value
                plugin.sync_downloaders = []
                plugin.get_data = MagicMock(return_value={})

                form_items, _ = plugin.get_form()

        vdialog = form_items[0]["content"][-1]
        vcard_text = vdialog["content"][0]["content"][1]
        dialog_body = vcard_text["content"]
        all_nodes = self._find_in_content(dialog_body)
        site_mapping_rows = [n for n in all_nodes if n.get("component") == "VRow" and "Example" in str(n)]
        seed_panels = [n for n in all_nodes if n.get("component") == "VExpansionPanel"]
        self.assertGreater(len(site_mapping_rows), 0)
        self.assertGreater(len(seed_panels), 0)


if __name__ == "__main__":
    unittest.main()
