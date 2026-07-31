#!/usr/bin/env python3
"""Generate an animated ASCII portrait SVG from a GitHub profile avatar."""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

RAMP = " .:-=+*#%@"
COLS = 64
ROW_RATIO = 0.50
FONT_SIZE = 12.0
CHAR_WIDTH = 7.2
LINE_HEIGHT = 14.0
PADDING = 12.0
ROW_DELAY = 0.055
LIGHT = "#6e7681"
DARK = "#c9d1d9"
FONT_FAMILY = (
    "ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"
    "'Liberation Mono','Courier New',monospace"
)


def request_bytes(url: str, token: str | None = None) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "cwee-profile-portrait",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_avatar(login: str, token: str | None = None) -> Image.Image:
    profile_bytes = request_bytes(f"https://api.github.com/users/{login}", token)
    profile = json.loads(profile_bytes.decode("utf-8"))
    avatar_url = profile.get("avatar_url")
    if not avatar_url:
        raise RuntimeError(f"GitHub profile for {login!r} has no avatar_url")
    avatar_bytes = request_bytes(f"{avatar_url}&s=512", token)
    return Image.open(io.BytesIO(avatar_bytes)).convert("RGB")


def prepare(image: Image.Image, cols: int) -> Image.Image:
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))

    rows = max(1, round(cols * ROW_RATIO))
    image = image.resize((cols, rows), Image.Resampling.LANCZOS)
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3))
    image = ImageEnhance.Contrast(image).enhance(1.18)
    return image


def ascii_lines(image: Image.Image, cols: int) -> list[str]:
    prepared = prepare(image, cols)
    rows = prepared.height
    pixels = prepared.load()
    lines: list[str] = []

    cx = (cols - 1) / 2
    cy = (rows - 1) / 2
    rx = cols / 2
    ry = rows / 2

    for y in range(rows):
        chars: list[str] = []
        for x in range(cols):
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            if dx * dx + dy * dy > 1.0:
                chars.append(" ")
                continue
            value = pixels[x, y]
            index = round((255 - value) / 255 * (len(RAMP) - 1))
            chars.append(RAMP[index])
        lines.append("".join(chars).rstrip())

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise RuntimeError("Avatar produced an empty ASCII portrait")
    return lines


def build_svg(lines: list[str], cols: int) -> str:
    width = cols * CHAR_WIDTH + PADDING * 2
    height = len(lines) * LINE_HEIGHT + PADDING * 2
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
            f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
            f'font-family="{html.escape(FONT_FAMILY, quote=True)}">'
        ),
        (
            f'<style>.portrait{{fill:{LIGHT}}}'
            f'@media(prefers-color-scheme:dark){{.portrait{{fill:{DARK}}}}}</style>'
        ),
    ]

    for row, line in enumerate(lines):
        y = PADDING + row * LINE_HEIGHT
        begin = row * ROW_DELAY
        visible_width = max(len(line), 1) * CHAR_WIDTH
        safe = html.escape(line)
        clip_id = f"portrait-row-{row}"
        parts.extend(
            [
                (
                    f'<clipPath id="{clip_id}"><rect x="{PADDING:.1f}" y="{y:.1f}" '
                    f'height="{LINE_HEIGHT:.1f}" width="0">'
                    f'<animate attributeName="width" from="0" to="{visible_width:.1f}" '
                    f'begin="{begin:.3f}s" dur="{ROW_DELAY:.3f}s" fill="freeze"/>'
                    "</rect></clipPath>"
                ),
                (
                    f'<g clip-path="url(#{clip_id})"><text xml:space="preserve" '
                    f'x="{PADDING:.1f}" y="{y + FONT_SIZE:.1f}" class="portrait" '
                    f'font-size="{FONT_SIZE:.1f}">{safe}</text></g>'
                ),
                (
                    f'<rect y="{y + 1:.1f}" width="5.5" height="{FONT_SIZE:.1f}" '
                    f'class="portrait" opacity="0">'
                    f'<animate attributeName="x" from="{PADDING:.1f}" '
                    f'to="{PADDING + visible_width:.1f}" begin="{begin:.3f}s" '
                    f'dur="{ROW_DELAY:.3f}s" fill="freeze"/>'
                    f'<set attributeName="opacity" to=".72" begin="{begin:.3f}s"/>'
                    f'<set attributeName="opacity" to="0" '
                    f'begin="{begin + ROW_DELAY:.3f}s"/></rect>'
                ),
            ]
        )

    parts.append("</svg>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default=os.environ.get("GH_LOGIN", "Chin-Wee"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--input", type=Path, help="Optional local image for testing")
    parser.add_argument("--output", type=Path, default=Path("ascii.svg"))
    parser.add_argument("--cols", type=int, default=COLS)
    args = parser.parse_args()

    if args.cols < 24 or args.cols > 120:
        parser.error("--cols must be between 24 and 120")

    if args.input:
        image = Image.open(args.input).convert("RGB")
    else:
        image = fetch_avatar(args.login, args.token)

    output = build_svg(ascii_lines(image, args.cols), args.cols)
    args.output.write_text(output, encoding="utf-8")
    print(f"wrote {args.output} from {args.login} at {args.cols} columns")


if __name__ == "__main__":
    main()
