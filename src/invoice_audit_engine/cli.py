"""
Command-Line Interface (CLI) for Invoice Audit Engine.
"""

import argparse
import os
import sys
import multiprocessing

try:
    from .pipeline import run_batch_audit, process_document
except ImportError:
    from invoice_audit_engine.pipeline import run_batch_audit, process_document

VERSION = "1.0.0"

LICENSE_TEXT = f"""
================================================================================
  INVOICE AUDIT ENGINE - MIT LICENSE
================================================================================
Copyright (c) 2026 Majid Mumtaz (CIA, ACA, FCCA)
GitHub: https://github.com/majidrajpar/invoice-audit-engine

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
================================================================================
"""

BANNER = f"""{"=" * 74}
  INVOICE AUDIT ENGINE | Continuous Document Telemetry v{VERSION}
  Architect : Majid Mumtaz (CIA, ACA, FCCA)
  License   : MIT License
{"=" * 74}"""


def print_license():
    print(LICENSE_TEXT.strip())


def main():
    multiprocessing.freeze_support()

    raw_args = sys.argv[1:]

    # Immediate license check if user calls --license as first arg
    if "--license" in raw_args:
        print_license()
        sys.exit(0)

    is_double_click = len(raw_args) == 0
    is_drag_drop = len(raw_args) > 0 and not any(a.startswith("-") for a in raw_args)
    should_pause_at_exit = is_double_click or is_drag_drop

    input_paths = []
    output_excel = "audit_report.xlsx"
    db_path = "audit_ledger.db"
    workers = None
    deskew = True

    if is_double_click:
        print(BANNER)
        print("\nInteractive mode. Drag and drop an invoice folder or image file into this window,")
        print("or enter the target path below.\n")

        try:
            user_input = input("Enter path to invoice folder or image file: ").strip()
            user_input = user_input.strip('"').strip("'")

            if not user_input:
                print("\n[Exit] No path entered.")
                try:
                    input("\nPress Enter to close this window...")
                except (EOFError, KeyboardInterrupt):
                    pass
                sys.exit(0)

            input_paths = [user_input]
            primary = os.path.abspath(user_input)
            base_dir = primary if os.path.isdir(primary) else os.path.dirname(primary)
            default_out = os.path.join(base_dir, "audit_report.xlsx")
            custom_out = input(f"Enter output Excel path [default: {default_out}]: ").strip()
            custom_out = custom_out.strip('"').strip("'")
            output_excel = custom_out if custom_out else default_out
            db_path = None
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

    elif is_drag_drop:
        print(BANNER)
        print("\nTarget documents detected via drag-and-drop.")
        input_paths = [p.strip('"').strip("'") for p in raw_args]
        primary = os.path.abspath(input_paths[0])
        base_dir = primary if os.path.isdir(primary) else os.path.dirname(primary)
        output_excel = os.path.join(base_dir, "audit_report.xlsx")
        db_path = None

    else:
        parser = argparse.ArgumentParser(
            prog="invoice-audit",
            description=f"Air-Gapped Continuous Controls Monitoring & Invoice Document Audit Engine v{VERSION} | Architect: Majid Mumtaz (CIA, ACA, FCCA) | License: MIT",
            epilog="Licensed under MIT License | Developed by Majid Mumtaz | https://github.com/majidrajpar/invoice-audit-engine"
        )
        parser.add_argument(
            "-i", "--input",
            nargs="+",
            required=True,
            help="One or more input directories or image file paths to audit."
        )
        parser.add_argument(
            "-o", "--output",
            default=None,
            help="Path for generated Excel audit workbook (default: <input_folder>/audit_report.xlsx)."
        )
        parser.add_argument(
            "--db",
            default=None,
            help="Optional path for SQLite checkpoint ledger database (default: None, tests current folder fresh)."
        )
        parser.add_argument(
            "-w", "--workers",
            type=int,
            default=None,
            help="Number of parallel worker processes (default: auto-detected CPU cores)."
        )
        parser.add_argument(
            "--no-deskew",
            action="store_true",
            help="Disable automatic image deskewing."
        )
        parser.add_argument(
            "--license",
            action="store_true",
            help="Display complete MIT License terms and copyright."
        )
        parser.add_argument(
            "-v", "--version",
            action="version",
            version=f"invoice-audit v{VERSION} (MIT License) | Architect: Majid Mumtaz (CIA, ACA, FCCA)"
        )

        args = parser.parse_args()

        if args.license:
            print_license()
            sys.exit(0)

        input_paths = args.input
        output_excel = args.output
        db_path = args.db
        workers = args.workers
        deskew = not args.no_deskew

    try:
        run_batch_audit(
            input_paths=input_paths,
            output_excel=output_excel,
            db_path=db_path,
            max_workers=workers,
            deskew=deskew
        )
        if should_pause_at_exit:
            try:
                input("\n[Complete] Audit finished. Press Enter to close this window...")
            except (EOFError, KeyboardInterrupt):
                pass
    except KeyboardInterrupt:
        print("\n[Interrupted] Run halted. Progress saved in checkpoint ledger.")
        if should_pause_at_exit:
            try:
                input("\nPress Enter to close this window...")
            except (EOFError, KeyboardInterrupt):
                pass
        sys.exit(1)
    except Exception as e:
        print(f"\n[Error] {e}")
        if should_pause_at_exit:
            try:
                input("\nPress Enter to close this window...")
            except (EOFError, KeyboardInterrupt):
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
