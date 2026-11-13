# Build with .venv-build-mac/bin/python -m PyInstaller MSU2Controller.spec
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'desktop_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'examples' / 'offline-demo.gif'), 'examples'),
           (str(root / 'assets' / 'licenses'), 'licenses'),
           (str(root / 'LICENSE'), 'licenses/MSU2Controller')],
    hiddenimports=['PIL._tkinter_finder', 'serial.tools.list_ports_osx'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='MSU2Controller', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, argv_emulation=False,
    target_arch='arm64', codesign_identity=None, entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MSU2Controller')
app = BUNDLE(
    collection, name='MSU2 小屏控制器.app',
    icon=str(root / 'assets' / 'MSU2.icns'),
    bundle_identifier='com.msu2.controller',
    info_plist={
        'CFBundleDisplayName': 'MSU2 小屏控制器',
        'CFBundleShortVersionString': '2026.11.13',
        'CFBundleVersion': '20261113.3',
        'LSMinimumSystemVersion': '13.0',
        'LSApplicationCategoryType': 'public.app-category.utilities',
        'NSHighResolutionCapable': True,
        'NSScreenCaptureUsageDescription': '投屏功能将主屏幕画面缩放后显示到连接的 MSU2 小屏。',
    },
)
