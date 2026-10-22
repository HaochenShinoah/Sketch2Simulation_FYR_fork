"""Assemble the script that the executor runs.

The configuration (or fixed) script carries the whole file, but four regions
are owned by the template and must never come from a model: the file head and
Step 1 (imports, HYSYS start-up with its retry and PID log, up to the
``# === Step 2`` marker), the helper block (Step 3b, between the
``# === Step 3b`` and ``# === Step 4`` markers) and the save step (from
``# === Step 7`` to the end). They are taken from the basis output, which the
Basis agent's safety belt copies verbatim from the template (only the case name
is model-supplied), so a Fixer that edits them has no effect: it can change
Steps 2-3 (property package, components) and Steps 4-6 (units, streams,
connections).
"""

from __future__ import annotations

from pathlib import Path

STEP_2_MARKER = "# === Step 2:"
STEP_3B_MARKER = "# === Step 3b:"
STEP_4_MARKER = "# === Step 4:"
STEP_7_MARKER = "# === Step 7:"
_MARKERS = (STEP_2_MARKER, STEP_3B_MARKER, STEP_4_MARKER, STEP_7_MARKER)


def _marker_index(text: str, marker: str, source: str) -> int:
    count = text.count(marker)
    if count != 1:
        raise ValueError(
            f"{source}: expected the marker {marker!r} exactly once, found {count}"
        )
    return text.index(marker)


def merge_script_texts(basis_code: str, configuration_code: str) -> str:
    """Steps 2-3 and 4-6 from the configuration script; the rest from the basis."""
    b = [_marker_index(basis_code, marker, "basis script") for marker in _MARKERS]
    c = [
        _marker_index(configuration_code, marker, "configuration script")
        for marker in _MARKERS
    ]
    if not (b == sorted(b) and c == sorted(c)):
        raise ValueError("Step markers are out of order in the scripts to merge.")
    b2, b3b, b4, b7 = b
    c2, c3b, c4, c7 = c
    return (
        basis_code[:b2]
        + configuration_code[c2:c3b]
        + basis_code[b3b:b4]
        + configuration_code[c4:c7]
        + basis_code[b7:]
    )


def merge_basis_and_configuration(
    basis_path: str,
    configuration_path: str,
    output_path: str,
) -> None:
    basis_file = Path(basis_path)
    configuration_file = Path(configuration_path)

    if not basis_file.exists():
        raise FileNotFoundError(f"Basis file not found: {basis_file}")
    if not configuration_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {configuration_file}")

    final_code = merge_script_texts(
        basis_file.read_text(encoding="utf-8"),
        configuration_file.read_text(encoding="utf-8"),
    )
    Path(output_path).write_text(final_code, encoding="utf-8")

    print(f"Merged script written to: {Path(output_path).resolve()}")
