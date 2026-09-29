# -*- coding: utf-8 -*-
"""Discover standard Revit 2021 metric family templates; Python 2.7 compatible."""
import os

MECHANICAL_NAMES = (
    u"metric mechanical equipment.rft",
    u"метрическая система, оборудование.rft",
    u"метрическая система, механическое оборудование.rft",
)
GENERIC_NAMES = (u"metric generic model.rft", u"метрическая система, типовая модель.rft")


def find_template(configured_path=None, roots=None):
    if roots is None:
        roots = [
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                         "Autodesk", "RVT 2021", "Family Templates"),
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                         "Autodesk", "Revit 2021", "Family Templates"),
        ]
    roots = sorted(set(os.path.abspath(path) for path in roots if os.path.isdir(path)))
    candidates = []
    total = 0
    for root in roots:
        for directory, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(files):
                lower = name.lower()
                if not lower.endswith(".rft"):
                    continue
                total += 1
                kind = "mechanical" if lower in MECHANICAL_NAMES else (
                    "generic_metric" if lower in GENERIC_NAMES else None)
                if kind:
                    candidates.append({"path": os.path.join(directory, name), "kind": kind})
    if not candidates:
        raise RuntimeError("No standard metric family template found in Revit 2021 directories")
    configured = os.path.normcase(os.path.abspath(configured_path)) if configured_path else None

    def rank(item):
        path = item["path"]
        configured_match = configured and os.path.normcase(os.path.dirname(path)) == configured
        return (0 if item["kind"] == "mechanical" else 1,
                0 if configured_match else 1,
                0 if os.path.basename(path).lower().startswith("metric ") else 1, path)

    candidates.sort(key=rank)
    return {"selected": candidates[0], "candidates": candidates,
            "roots": roots, "rft_files_found": total,
            "configured_path": configured_path}
