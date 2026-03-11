from PyInstaller.compat import is_darwin, is_win
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

if is_win:
    hiddenimports = ["OpenGL.platform.win32"]
elif is_darwin:
    hiddenimports = ["OpenGL.platform.darwin"]
else:
    hiddenimports = ["OpenGL.platform.glx"]

hiddenimports += collect_submodules("OpenGL.arrays")

# Exclude legacy vendor DLL bundle that depends on MSVCR90 and is not required
# for standard OpenGL rendering path in this app.
if is_win:
    datas = collect_data_files("OpenGL", excludes=["**/DLLS/*"])
