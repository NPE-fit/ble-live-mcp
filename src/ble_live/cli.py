"""Command line: generate firmware sources, convert between xlsx and JSON, browse the spec."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import spec
from .config import LiveConfig
from .generator import generate, write_sources
from .xlsx import export_xlsx, import_xlsx


def load_config(path: str | Path) -> LiveConfig:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return import_xlsx(path)
    return LiveConfig.load_json(path)


def cmd_generate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 1
    out_dir = Path(args.out or args.templates)
    for path in write_sources(generate(cfg, args.templates), out_dir):
        print(f"wrote {path}")
    return 0


def cmd_import_xlsx(args: argparse.Namespace) -> int:
    cfg = import_xlsx(args.xlsx)
    cfg.spec_version = cfg.spec_version or spec.version()
    if args.output:
        cfg.save_json(args.output)
        print(f"wrote {args.output}")
    else:
        print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def cmd_export_xlsx(args: argparse.Namespace) -> int:
    export_xlsx(load_config(args.config), args.output)
    print(f"wrote {args.output}")
    return 0


def cmd_spec(args: argparse.Namespace) -> int:
    if args.what == "version":
        print(spec.version())
    else:
        print(json.dumps(spec.fields(args.category), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ble-live", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="render ble_live.c/.h from a config (xlsx or JSON)")
    g.add_argument("config")
    g.add_argument(
        "--templates",
        default=os.environ.get("BLE_LIVE_ROOT", "."),
        help="directory holding ble_live_base.c/.h (default: $BLE_LIVE_ROOT or .)",
    )
    g.add_argument("--out", help="output directory (default: the templates directory)")
    g.set_defaults(func=cmd_generate)

    i = sub.add_parser("import-xlsx", help="convert live.xlsx to JSON")
    i.add_argument("xlsx")
    i.add_argument("-o", "--output")
    i.set_defaults(func=cmd_import_xlsx)

    e = sub.add_parser("export-xlsx", help="write a config (xlsx or JSON) as a live.xlsx workbook")
    e.add_argument("config")
    e.add_argument("-o", "--output", required=True)
    e.set_defaults(func=cmd_export_xlsx)

    s = sub.add_parser("spec", help="print spec tables")
    s_sub = s.add_subparsers(dest="what", required=True)
    s_sub.add_parser("version")
    f = s_sub.add_parser("fields")
    f.add_argument("category", choices=spec.CATEGORIES)
    s.set_defaults(func=cmd_spec)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
