# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller gitfinder.spec
#
# Run `npm run build` inside frontend/ BEFORE running this, so frontend/dist
# exists and gets bundled alongside the app.

from PyInstaller.utils.hooks import collect_submodules
app_hiddenimports = collect_submodules("app")
block_cipher = None

a = Analysis(
    ['app/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('frontend/dist', 'frontend/dist'),  # the built UI
    ],
    hiddenimports=[
    *app_hiddenimports,
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GitFinder',
    debug=False,
    strip=False,
    upx=True,
    console=False,          # windowed app, no terminal window
    icon=None,              # set to 'icon.icns' (mac) or 'icon.ico' (windows) once you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='GitFinder',
)

# macOS only: also produce a proper .app bundle
app = BUNDLE(
    coll,
    name='GitFinder.app',
    icon=None,              # 'icon.icns' once you have one
    bundle_identifier='com.mattyou7.gitfinder',
)
