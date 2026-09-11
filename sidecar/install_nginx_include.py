"""Add the usage sidecar locations include to an existing Nginx server block.

The installer is parameterized so a checkout never contains a deployment
domain or a machine-specific path.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone
import shutil


DEFAULT_CONF = "/etc/nginx/conf.d/context-tree.conf"
DEFAULT_INCLUDE = "/etc/nginx/snippets/context-tree-usage-sidecar.locations.conf"
DEFAULT_MARKER = "    location ^~ /v1/ {"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nginx-conf", default=DEFAULT_CONF,
        help=f"server config to update (default: {DEFAULT_CONF})",
    )
    parser.add_argument(
        "--include-path", default=DEFAULT_INCLUDE,
        help=f"absolute path used in the Nginx include (default: {DEFAULT_INCLUDE})",
    )
    parser.add_argument(
        "--marker", default=DEFAULT_MARKER,
        help="first line before which the include is inserted",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show the proposed config without writing it",
    )
    return parser.parse_args()


def install(conf: Path, include_path: str, marker: str, dry_run: bool = False) -> Path | None:
    text = conf.read_text(encoding="utf-8")
    include = f"    include {include_path};\n"
    if include.strip() in text:
        print("include already present")
        return None
    marker_line = marker if marker.endswith("\n") else marker + "\n"
    if marker_line not in text:
        raise SystemExit(f"marker not found in {conf}: {marker!r}")
    updated = text.replace(marker_line, include + "\n" + marker_line, 1)
    if dry_run:
        print(updated)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = conf.with_suffix(conf.suffix + f".pre-context-tree-{stamp}.bak")
    shutil.copy2(conf, backup)
    conf.write_text(updated, encoding="utf-8")
    print(f"backup={backup}")
    return backup


def main() -> None:
    args = parse_args()
    install(Path(args.nginx_conf).expanduser(), args.include_path, args.marker, args.dry_run)


if __name__ == "__main__":
    main()
