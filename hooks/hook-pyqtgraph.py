from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Keep runtime assets but skip bulky examples payload.
datas = collect_data_files("pyqtgraph", excludes=["**/examples/*"])

# Keep template modules needed by pyqtgraph UI internals, but do not recurse
# into pyqtgraph.jupyter to avoid optional jupyter_rfb dependency warnings.
all_imports = collect_submodules(
    "pyqtgraph",
    filter=lambda name: name != "pyqtgraph.examples"
    and not name.startswith("pyqtgraph.jupyter"),
)
hiddenimports = [name for name in all_imports if "Template" in name]
hiddenimports += ["pyqtgraph.multiprocess.bootstrap"]

try:
    from PyInstaller.utils.hooks.qt import exclude_extraneous_qt_bindings
except ImportError:
    pass
else:
    excludedimports = exclude_extraneous_qt_bindings(
        hook_name="hook-pyqtgraph",
        qt_bindings_order=None,
    )
