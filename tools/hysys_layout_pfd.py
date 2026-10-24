"""Lay out the PFD of an existing HYSYS case (graphics only) and prove the
simulation did not change.

    python tools/hysys_layout_pfd.py outputs/cs4/Case.hsc
    python tools/hysys_layout_pfd.py Case.hsc --out Case_layout.hsc --verify --screenshot Case.png

Opens the case in its own HYSYS instance (close other HYSYS windows first:
a script started while one is open attaches to it), shows the case window
(the PFD exists only then), arranges units left to right in process order
with feeds on the left, products on the right and recycles on their own
lanes, saves a copy (default: ``<name>_layout.hsc`` next to the original)
and quits. ``--verify`` reopens the copy and compares operations, attachments,
stream names and values, fluid package and components with the original
(expected: no difference). ``--screenshot`` grabs the HYSYS window.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sketch2sim.pfd_layout import (  # noqa: E402
    case_signature,
    layout_case,
    signature_differences,
)


def hysys_pids() -> set[int]:
    raw = subprocess.run(
        [
            "tasklist",
            "/FI",
            "IMAGENAME eq AspenHysys.exe",
            "/FI",
            "STATUS eq RUNNING",
            "/FO",
            "CSV",
        ],
        capture_output=True,
    ).stdout.decode("mbcs", "replace")
    return {
        int(line.split('","')[1])
        for line in raw.splitlines()
        if line.startswith('"AspenHysys.exe"')
    }


def make_pump(hysys):
    def pump(seconds: float = 0.5) -> None:
        end = time.time() + seconds
        while time.time() < end:
            try:
                hysys.DoEvents()
            except Exception:
                pass
            time.sleep(0.1)

    return pump


def show(case, pump) -> None:
    case.Visible = True
    try:
        case.Activate()
    except Exception:
        pass
    pump(2.0)


def _foreground_hysys(pids: set[int]):
    """Bring the main HYSYS window (of the given processes) to the front,
    maximised; returns its screen rectangle or None."""
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError:
        return None
    found: list[int] = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids:
                found.append(hwnd)

    win32gui.EnumWindows(visit, None)
    if not found:
        return None

    def area(hwnd):
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return (right - left) * (bottom - top)

    hwnd = max(found, key=area)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        try:  # Windows lets a process take the foreground after an Alt press
            from win32com.client import Dispatch

            Dispatch("WScript.Shell").SendKeys("%")
        except Exception:
            pass
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(1.0)
    return win32gui.GetWindowRect(hwnd)


def screenshot(hysys, path: str, pump, pids: set[int] | None = None) -> bool:
    try:
        from PIL import ImageGrab
    except ImportError:
        print("[pfd] Pillow is not installed; no screenshot")
        return False
    pump(1.0)
    try:
        box = _foreground_hysys(pids or set())
        pump(1.0)
        if box is None:
            left, top = int(hysys.Left), int(hysys.Top)
            box = (left, top, left + int(hysys.Width), top + int(hysys.Height))
        ImageGrab.grab(bbox=box).save(path)
    except Exception as error:
        print(f"[pfd] screenshot failed: {str(error)[:120]}")
        return False
    print(f"[pfd] screenshot written to {path}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case", help="the .hsc to lay out (never modified)")
    parser.add_argument("--out", help="where to save the laid-out copy")
    parser.add_argument(
        "--verify", action="store_true", help="reopen the copy and compare"
    )
    parser.add_argument("--screenshot", help="PNG of the HYSYS window after the layout")
    parser.add_argument(
        "--preview",
        help="PNG drawn from the final positions (boxes and lines, no HYSYS rendering)",
    )
    parser.add_argument(
        "--hide-helper-labels",
        action="store_true",
        help="hide the labels of normaliser-made streams (Mixer outlet to ..., Burner feed to ...)",
    )
    parser.add_argument("--keep-open", action="store_true", help="leave HYSYS open")
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing --out file"
    )
    args = parser.parse_args(argv)

    source = os.path.abspath(args.case)
    if not os.path.isfile(source):
        print(f"not a file: {source}")
        return 2
    out = os.path.abspath(args.out or os.path.splitext(source)[0] + "_layout.hsc")
    same_file = os.path.normcase(os.path.realpath(out)) == os.path.normcase(
        os.path.realpath(source)
    )
    if same_file:
        print("--out must differ from the original case (same file)")
        return 2
    if os.path.exists(out) and not args.force:
        print(f"{out} exists; pass --force to overwrite it")
        return 2
    report_path = os.path.splitext(out)[0] + "_pfd.json"

    from win32com.client import DispatchEx

    before = hysys_pids()
    hysys = DispatchEx("HYSYS.Application")
    hysys.Visible = True
    new_pids = sorted(hysys_pids() - before)
    print(
        f"[hysys] pid={','.join(map(str, new_pids)) or '? (attached to a running instance)'}"
    )
    pump = make_pump(hysys)
    exit_code = 0
    try:
        case = None
        for _ in range(12):
            try:
                case = hysys.SimulationCases.Open(source)
                break
            except Exception as error:
                print(f"[hysys] Open not ready ({str(error)[:80]}); retry in 5 s")
                time.sleep(5)
        if case is None:
            print("HYSYS did not open the case")
            return 1
        show(case, pump)
        signature_before = case_signature(case)
        report = layout_case(
            case,
            pump=pump,
            hide_helper_labels=args.hide_helper_labels,
            preview_path=os.path.abspath(args.preview) if args.preview else None,
        )
        if report is None:
            exit_code = 1
        else:
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=1, default=str)
            print(f"[pfd] layout report written to {report_path}")
        if args.screenshot:
            screenshot(hysys, os.path.abspath(args.screenshot), pump, set(new_pids))
        if os.path.exists(out):
            os.remove(out)
        case.SaveAs(out)
        print(f"[pfd] laid-out copy saved at {out}")
        if args.verify:
            try:
                case.Close(False)
            except Exception:
                pass
            pump(2.0)
            copy = hysys.SimulationCases.Open(out)
            show(copy, pump)
            differences = signature_differences(signature_before, case_signature(copy))
            if differences:
                exit_code = 3
                print(
                    f"[verify] {len(differences)} difference(s) between the original and the copy:"
                )
                for line in differences:
                    print(f"  - {line}")
            else:
                ops = len(signature_before["operations"])
                streams = len(signature_before["streams"])
                print(
                    f"[verify] identical simulation: {ops} operations with their attachments, "
                    f"{streams} streams with their values, fluid package and components"
                )
    finally:
        if not args.keep_open:
            try:
                hysys.Quit()
            except Exception:
                pass
            for _ in range(30):
                if not (hysys_pids() - before):
                    break
                time.sleep(1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
