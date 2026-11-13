"""Installed apps must keep writes outside their signed resource directory."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app_paths
import notify_client


class ApplicationPathsTests(unittest.TestCase):
    def test_source_run_keeps_existing_local_data(self):
        with patch.object(app_paths.sys, 'frozen', False, create=True):
            self.assertEqual(app_paths.data_directory(), Path(app_paths.__file__).resolve().parent)
            self.assertEqual(app_paths.log_directory(), app_paths.data_directory() / 'logs')

    def test_frozen_mac_writes_to_user_support_and_logs(self):
        with patch.object(app_paths.sys, 'frozen', True, create=True), \
             patch.object(app_paths.sys, 'platform', 'darwin'), \
             patch.object(Path, 'home', return_value=Path('/user')):
            self.assertEqual(app_paths.data_directory(), Path('/user/Library/Application Support/MSU2Controller'))
            self.assertEqual(app_paths.log_directory(), Path('/user/Library/Logs/MSU2Controller'))

    def test_frozen_windows_respects_local_app_data(self):
        with patch.object(app_paths.sys, 'frozen', True, create=True), \
             patch.object(app_paths.sys, 'platform', 'win32'), \
             patch.dict(app_paths.os.environ, {'LOCALAPPDATA': '/user/local'}):
            self.assertEqual(app_paths.data_directory(), Path('/user/local/MSU2Controller'))

    def test_standalone_sdk_finds_installed_mac_and_preserves_source_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            local = home / 'source' / '.runtime' / 'api.json'
            with patch.object(notify_client, 'DEFAULT_DISCOVERY', local), \
                 patch.object(notify_client.sys, 'platform', 'darwin'), \
                 patch.object(Path, 'home', return_value=home):
                self.assertEqual(notify_client.NotificationClient().discovery_path,
                                 home / 'Library/Application Support/MSU2Controller/.runtime/api.json')
                local.parent.mkdir(parents=True)
                local.write_text('{}')
                self.assertEqual(notify_client.NotificationClient().discovery_path, local)

    def test_explicit_sdk_path_always_wins(self):
        self.assertEqual(notify_client.NotificationClient('/chosen/api.json').discovery_path,
                         Path('/chosen/api.json'))
