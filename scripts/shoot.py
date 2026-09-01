#!/usr/bin/env python3
"""Screenshot pages from a running dev server, for visual review of UI changes.

The claude-in-chrome browser extension isn't available in this environment,
so this is the way to actually *see* a template/CSS change: point it at the
dev server (started via the `run-dev-server` skill) and read the PNGs it
writes.

Usage:
    python scripts/shoot.py /jobs /jobs?q=engineer --width 1200 820
    python scripts/shoot.py /jobs?status=new,accepted --base http://127.0.0.1:8952 --out temp/
    python scripts/shoot.py /jobs --click "select[name=org]" --out temp/

Each (path, width) pair is written to <out>/<slug>_<width>.png. Run with
`dangerouslyDisableSandbox: true` — it needs a real network connection to the
dev server.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_WIDTHS = [1200]
DEFAULT_HEIGHT = 950


def _slug(path: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")
    return slug or "root"


async def shoot(
    paths: list[str],
    *,
    base: str,
    widths: list[int],
    out_dir: Path,
    click: str | None,
    wait_ms: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for path in paths:
            url = base.rstrip("/") + path
            for width in widths:
                page = await browser.new_page(viewport={"width": width, "height": DEFAULT_HEIGHT})
                await page.goto(url, wait_until="networkidle")
                await page.wait_for_timeout(wait_ms)
                if click:
                    try:
                        await page.click(click)
                        await page.wait_for_timeout(wait_ms)
                    except Exception as exc:  # noqa: BLE001 - best-effort, report and continue
                        print(f"  click {click!r} failed: {exc}")
                name = f"{_slug(path)}_{width}.png"
                await page.screenshot(path=str(out_dir / name))
                print(f"wrote {out_dir / name}")
                await page.close()
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="Path(s) to screenshot, e.g. /jobs or /jobs?q=engineer")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="Dev server base URL")
    parser.add_argument("--width", type=int, nargs="+", default=DEFAULT_WIDTHS, help="Viewport width(s)")
    parser.add_argument("--out", type=Path, default=Path("temp"), help="Output directory")
    parser.add_argument("--click", default=None, help="Optional selector to click before screenshotting (e.g. to open a dropdown)")
    parser.add_argument("--wait", type=int, default=300, help="Extra wait (ms) after load/click, for CSS transitions")
    args = parser.parse_args()
    asyncio.run(shoot(args.paths, base=args.base, widths=args.width, out_dir=args.out, click=args.click, wait_ms=args.wait))


if __name__ == "__main__":
    main()
