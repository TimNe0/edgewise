# Exporting the app class here lets the simulator start Edgewise directly with
# `python run.py edgewise.EdgewiseApp`. The badge launcher does not need it --
# it imports `apps.<folder>.app` and reads `__app_export__` -- and desktop unit
# tests import the leaf modules without any firmware present, so the import is
# guarded rather than unconditional.
try:
    from .app import EdgewiseApp  # noqa: F401
except ImportError:  # pragma: no cover - firmware modules missing off-badge
    pass
