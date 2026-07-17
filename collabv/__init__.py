"""
CollabV backend package.

Several core matching-engine modules have been renamed on disk to
human-readable filenames (e.g. "Professors Selling & Buying Patents
Matching Engine.py") that are not valid Python module names - spaces,
parentheses, and "&" cannot appear in an import statement. Rather than
renaming the files back, this loads each one directly from its actual file
path via importlib and registers it in sys.modules under the canonical
dotted name every other module in this codebase already imports (e.g.
collabv.matching_engine_5) - so `from .matching_engine_5 import X`
elsewhere keeps working completely unmodified, regardless of what the file
on disk is actually called.

Load order matters: matching_engine_5 is foundational (matching_engine_7/8/9
and matching_engine_4 import shared helpers from it, and matching_engine_8
also imports from matching_engine_9), so dependencies are loaded first.

If a file is ever renamed back to its plain snake_case name, this loader
just skips it (see the `if dotted_name in sys.modules` / `if not path.exists()`
guards below) and Python's normal import machinery picks it up instead -
this mapping is a fallback, not a hard requirement.
"""
import importlib.util
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).parent

# (canonical dotted module name, actual on-disk filename), in dependency order.
_RENAMED_ENGINES = [
    ("collabv.matching_engine_5", "Professors Selling & Buying Patents Matching Engine.py"),
    ("collabv.matching_engine", "Company (Problems statements) to Professor Matching Engine.py"),
    ("collabv.matching_engine_2", "Professor to Company (Listed Problems statements) Matching Engine.py"),
    ("collabv.matching_engine_4", "Company Buying Patents Matching Engine.py"),
    ("collabv.matching_engine_9", "Students & Employees Job Postings Matching Engine.py"),
    ("collabv.matching_engine_7", "Student & Employee Buying Patents Matching Engine.py"),
    ("collabv.matching_engine_8", "Students & Employees Research Opportunities Matching Engine.py"),
]


def _load_renamed_engines() -> None:
    pkg = sys.modules[__name__]
    for dotted_name, filename in _RENAMED_ENGINES:
        if dotted_name in sys.modules:
            continue  # already loaded (or the plain-named file exists and got imported normally)
        path = _PKG_DIR / filename
        if not path.exists():
            continue  # tolerate a future rename-back without breaking startup
        spec = importlib.util.spec_from_file_location(dotted_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[dotted_name] = module
        setattr(pkg, dotted_name.rsplit(".", 1)[1], module)
        spec.loader.exec_module(module)


_load_renamed_engines()
