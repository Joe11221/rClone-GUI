# PyInstaller build definition. Run through packaging/build.ps1.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = [
    ("../app/templates", "app/templates"),
    ("../app/static", "app/static"),
    ("../desktop/assets", "desktop/assets"),
    ("bin/rclone-README.txt", "bin"),
    ("../LICENSE", "."),
]
# APScheduler finds its triggers and executors through package metadata.
datas += copy_metadata("APScheduler")
datas += collect_data_files("tzdata")

analysis = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=[("bin/rclone.exe", "bin")],
    datas=datas,
    hiddenimports=[
        "pystray._win32",
        *collect_submodules("apscheduler"),
    ],
    # pywebview's own hook collects its WebView2 and .NET files.
    excludes=["gunicorn", "pytest", "tkinter"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="rClone GUI",
    console=False,
    icon="../desktop/assets/icon.ico",
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="rClone GUI")
