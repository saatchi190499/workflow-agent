from PyInstaller.utils.hooks import collect_submodules, collect_all

# Собираем всё из зависимостей, которые могут импортироваться динамически
# (petex_client/pi_client подгружаются через RemoteModuleFinder)
pkgs_to_collect = ["petex_client", "pandas", "numpy"]
datas, binaries, hiddenimports = [], [], []
for pkg in pkgs_to_collect:
    da, bi, hi = collect_all(pkg)
    datas += da
    binaries += bi
    hiddenimports += hi

# COM/pywin32: win32com подхватывается, но добавим безопасно
hiddenimports += collect_submodules("win32com")
hiddenimports += ["pythoncom", "main"]  # 'main' обязательно, т.к. uvicorn грузит его строкой "main:app"

block_cipher = None

a_app = Analysis(
    ["run.py"],                  # точка входа — ваш run.py
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz_app = PYZ(a_app.pure, a_app.zipped_data, cipher=block_cipher)

exe_app = EXE(
    pyz_app,
    a_app.scripts,
    a_app.binaries,
    a_app.zipfiles,
    a_app.datas,
    name="WorkflowAgent",        # имя exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # True — с консолью (видно логи). Поставьте False, если не нужна консоль.
    icon='logo.ico',  
)