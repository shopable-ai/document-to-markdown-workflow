#!/usr/bin/env python3
"""Export validated conversion packages into one clean Markdown directory."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


IMAGE_RE = re.compile(r"(!\[[^\]]*\]\(\s*)(<?)([^>)\s]+)(>?)(\s*\))")


class ExportFailure(RuntimeError):
    """The reader-facing delivery cannot be produced without broken links."""


def _export_package(package: Path, output_root: Path) -> dict[str, object]:
    package = package.expanduser().resolve()
    source_markdown = package / "index.md"
    if not source_markdown.is_file() or not source_markdown.read_text(encoding="utf-8").strip():
        raise ExportFailure(f"missing or empty index.md: {package}")

    book_name = package.name
    copied_assets: list[str] = []
    output_root.mkdir(parents=True, exist_ok=True)
    book_asset_root = output_root / "assets" / book_name
    if book_asset_root.exists():
        shutil.rmtree(book_asset_root)

    def replace_image(match: re.Match[str]) -> str:
        raw = match.group(3)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) or raw.startswith("/"):
            return match.group(0)
        source_asset = (package / raw).resolve()
        if not source_asset.is_relative_to(package) or not source_asset.is_file():
            raise ExportFailure(f"missing or unsafe image reference in {source_markdown}: {raw}")
        try:
            asset_relative = source_asset.relative_to((package / "assets").resolve())
        except ValueError as exc:
            raise ExportFailure(f"image is outside package assets/: {raw}") from exc
        delivery_relative = Path("assets") / book_name / asset_relative
        destination = output_root / delivery_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_asset, destination)
        copied_assets.append(delivery_relative.as_posix())
        return f"{match.group(1)}{delivery_relative.as_posix()}{match.group(5)}"

    markdown = source_markdown.read_text(encoding="utf-8")
    exported_markdown = IMAGE_RE.sub(replace_image, markdown)
    destination_markdown = output_root / f"{book_name}.md"
    temporary = destination_markdown.with_suffix(".md.tmp")
    temporary.write_text(exported_markdown, encoding="utf-8")
    temporary.replace(destination_markdown)
    return {
        "package": str(package),
        "markdown": str(destination_markdown),
        "assets": sorted(set(copied_assets)),
    }


def export_collection(packages: list[Path], output_root: Path) -> dict[str, object]:
    output_root = output_root.expanduser().resolve()
    if not packages:
        raise ExportFailure("at least one package is required")
    names = [path.expanduser().resolve().name for path in packages]
    if len(names) != len(set(names)):
        raise ExportFailure("package names must be unique")
    outputs = [_export_package(package, output_root) for package in packages]
    return {
        "status": "passed",
        "output_root": str(output_root),
        "markdown_count": len(outputs),
        "outputs": outputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy one index.md per validated package into a clean shared directory"
    )
    parser.add_argument("--package", action="append", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = export_collection(args.package, args.output_root)
    except (OSError, ExportFailure) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
