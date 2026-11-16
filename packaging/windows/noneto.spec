# Build with NONETO_SOURCE pointing at an exported stable Windows revision.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

source = Path(os.environ['NONETO_SOURCE']).resolve()
support = Path(SPECPATH)
datas = [(str(source / 'LICENSE'), '.'), (str(source / 'README.md'), '.'),
         (str(source / 'noneto'), 'source/noneto')]
datas += collect_data_files('noneto')
datas += copy_metadata('noneto-cli', recursive=True)
binaries, hidden = [], ['webview.platforms.winforms', 'webview.platforms.edgechromium']
hidden += collect_submodules('textual.widgets')
hidden += collect_submodules('textual_image', filter=lambda name: '.demo' not in name)
for package in ('winrt', 'clr_loader', 'pythonnet'):
    data, binary, modules = collect_all(package, filter_submodules=lambda name: '_pyinstaller' not in name)
    datas += data
    binaries += binary
    hidden += modules

a = Analysis([str(support / 'entry.py')], pathex=[str(source), str(support)],
             binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['PyQt6', 'PyQt5', 'PySide6', 'PySide2', 'gi', 'qtpy',
                       'webview.platforms.cef', 'webview.platforms.gtk',
                       'webview.platforms.qt', 'webview.platforms.cocoa',
                       'noneto.ui.linux_login'],
             # Keep LGPL dependencies replaceable and ship their original Python sources.
             module_collection_mode={'tidalapi': 'py', 'textual_image': 'py'})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='noneto',
          console=True, upx=False, icon=str(source / 'noneto/assets/noneto.ico'),
          version=os.environ['NONETO_VERSION_FILE'])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='noneto')
