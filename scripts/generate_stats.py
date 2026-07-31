#!/usr/bin/env python3
"""Generate self-hosted SVG graphics for a GitHub profile-style README.

The script uses only Python's standard library. In normal mode it queries the
GitHub GraphQL API using GITHUB_TOKEN. In --demo mode it writes clearly marked
preview graphics so the repository renders before the first workflow refresh.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import random
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

API = "https://api.github.com/graphql"
WIDTH = 620
MONO = (
    "ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"
    "'Liberation Mono','Courier New',monospace"
)
RAMP = (" ", ":", "+", "#", "@")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")

LIGHT = {
    "data": "#6e7681",
    "emph": "#24292f",
    "dim": "#8c959f",
    "rule": "#d0d7de",
    "surface": "#ffffff",
}
DARK = {
    "data": "#c9d1d9",
    "emph": "#f0f6fc",
    "dim": "#8b949e",
    "rule": "#30363d",
    "surface": "#0d1117",
}

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            date
            weekday
          }
        }
      }
    }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      isFork: false
      privacy: PUBLIC
    ) {
      nodes {
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Streak:
    length: int
    start: str | None
    end: str | None


@dataclass(frozen=True)
class Summary:
    total: int
    active_days: int
    best_week: int
    weekly: tuple[int, ...]
    days: tuple[dict[str, Any], ...]
    current: Streak
    longest: Streak
    languages_by_size: tuple[tuple[str, int], ...]
    languages_by_repo: tuple[tuple[str, int], ...]
    preview: bool = False


def contribution_window() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    return (
        f"{start.isoformat()}T00:00:00Z",
        f"{today.isoformat()}T23:59:59Z",
    )


def fetch_profile(login: str, token: str) -> dict[str, Any]:
    start, end = contribution_window()
    request_body = json.dumps({
        "query": QUERY,
        "variables": {"login": login, "from": start, "to": end},
    }).encode("utf-8")
    request = urllib.request.Request(
        API,
        data=request_body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-profile-graphics",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")

    user = (payload.get("data") or {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user not found: {login}")
    return user


def demo_profile() -> dict[str, Any]:
    """Return deterministic preview data, explicitly marked in the SVG output."""
    rng = random.Random(20260731)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    days: list[dict[str, Any]] = []
    for offset in range(365):
        current = start + timedelta(days=offset)
        wave = max(0.0, math.sin(offset / 19.0) + 0.35)
        count = 0 if rng.random() < 0.46 else int(1 + wave * 4 + rng.random() * 3)
        days.append({
            "contributionCount": count,
            "date": current.isoformat(),
            "weekday": (current.weekday() + 1) % 7,
        })

    weeks: list[dict[str, Any]] = []
    for index in range(0, len(days), 7):
        weeks.append({"contributionDays": days[index:index + 7]})

    language_sets = [
        [("TypeScript", 820_000), ("JavaScript", 180_000), ("CSS", 55_000)],
        [("Python", 490_000), ("Jupyter Notebook", 260_000)],
        [("C#", 640_000), ("GDScript", 210_000)],
        [("TypeScript", 510_000), ("HTML", 90_000)],
        [("Python", 320_000), ("Shell", 35_000)],
    ]
    repos = []
    for language_set in language_sets:
        repos.append({
            "languages": {
                "edges": [
                    {"size": size, "node": {"name": name}}
                    for name, size in language_set
                ]
            }
        })

    return {
        "_preview": True,
        "contributionsCollection": {
            "contributionCalendar": {
                "totalContributions": sum(d["contributionCount"] for d in days),
                "weeks": weeks,
            }
        },
        "repositories": {"nodes": repos},
    }


def calculate_streaks(days: Iterable[dict[str, Any]]) -> tuple[Streak, Streak]:
    ordered = list(days)
    best = Streak(0, None, None)
    run_length = 0
    run_start: str | None = None

    for day in ordered:
        if day["contributionCount"] > 0:
            run_length += 1
            run_start = run_start or day["date"]
            if run_length > best.length:
                best = Streak(run_length, run_start, day["date"])
        else:
            run_length = 0
            run_start = None

    tail = ordered[:-1] if ordered and ordered[-1]["contributionCount"] == 0 else ordered
    current_length = 0
    current_start: str | None = None
    current_end: str | None = None
    for day in reversed(tail):
        if day["contributionCount"] == 0:
            break
        current_length += 1
        current_start = day["date"]
        current_end = current_end or day["date"]

    return Streak(current_length, current_start, current_end), best


def rank_languages(repositories: Iterable[dict[str, Any]]) -> tuple[
    tuple[tuple[str, int], ...],
    tuple[tuple[str, int], ...],
]:
    by_size: Counter[str] = Counter()
    by_repo: Counter[str] = Counter()

    for repository in repositories:
        edges = ((repository.get("languages") or {}).get("edges") or [])
        for edge in edges:
            by_size[edge["node"]["name"]] += int(edge["size"])
        if edges:
            by_repo[edges[0]["node"]["name"]] += 1

    size_rank = tuple(sorted(by_size.items(), key=lambda item: (-item[1], item[0]))[:5])
    repo_rank = tuple(sorted(by_repo.items(), key=lambda item: (-item[1], item[0]))[:5])
    return size_rank, repo_rank


def summarise(profile: dict[str, Any]) -> Summary:
    calendar = profile["contributionsCollection"]["contributionCalendar"]
    weeks = [week["contributionDays"] for week in calendar["weeks"]]
    days = tuple(day for week in weeks for day in week)
    weekly = tuple(sum(day["contributionCount"] for day in week) for week in weeks)
    current, longest = calculate_streaks(days)
    by_size, by_repo = rank_languages(profile["repositories"]["nodes"])

    return Summary(
        total=int(calendar["totalContributions"]),
        active_days=sum(1 for day in days if day["contributionCount"] > 0),
        best_week=max(weekly, default=0),
        weekly=weekly,
        days=days,
        current=current,
        longest=longest,
        languages_by_size=by_size,
        languages_by_repo=by_repo,
        preview=bool(profile.get("_preview")),
    )


def css() -> str:
    def theme(values: dict[str, str]) -> str:
        return (
            f".data-fill{{fill:{values['data']}}}"
            f".data-stroke{{stroke:{values['data']}}}"
            f".emph-fill{{fill:{values['emph']}}}"
            f".dim-fill{{fill:{values['dim']}}}"
            f".rule-stroke{{stroke:{values['rule']}}}"
            f".surface-stroke{{stroke:{values['surface']}}}"
        )

    return (
        "<style>"
        + theme(LIGHT)
        + ".area-fill{fill:#6e7681;opacity:.13}"
        + "@media(prefers-color-scheme:dark){"
        + theme(DARK)
        + ".area-fill{fill:#c9d1d9;opacity:.16}"
        + "}"
        + "</style>"
    )


def svg_open(height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
        f'height="{height}" viewBox="0 0 {WIDTH} {height}" fill="none" '
        f'font-family="{html.escape(MONO, quote=True)}">'
        + css()
    )


def label(
    x: float,
    y: float,
    text: object,
    size: int = 11,
    class_name: str = "dim-fill",
    anchor: str = "start",
    weight: int | None = None,
) -> str:
    attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'font-size="{size}"', f'class="{class_name}"']
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if weight:
        attrs.append(f'font-weight="{weight}"')
    return f"<text {' '.join(attrs)}>{html.escape(str(text))}</text>"


def fade(begin: float, duration: float = 0.45) -> str:
    return (
        f'<animate attributeName="opacity" from="0" to="1" '
        f'begin="{begin:.2f}s" dur="{duration:.2f}s" fill="freeze"/>'
    )


def reveal_clip(identifier: str, x: float, y: float, width: float, height: float,
                begin: float = 0.45, duration: float = 1.25) -> tuple[str, str]:
    clip = (
        f'<clipPath id="{identifier}"><rect x="{x:.1f}" y="{y:.1f}" '
        f'width="0" height="{height:.1f}">'
        f'<animate attributeName="width" from="0" to="{width:.1f}" '
        f'begin="{begin:.2f}s" dur="{duration:.2f}s" fill="freeze"/>'
        "</rect></clipPath>"
    )
    cursor = (
        f'<rect y="{y:.1f}" width="2" height="{height:.1f}" '
        f'class="data-fill" opacity="0">'
        f'<animate attributeName="x" from="{x:.1f}" to="{x + width:.1f}" '
        f'begin="{begin:.2f}s" dur="{duration:.2f}s" fill="freeze"/>'
        f'<set attributeName="opacity" to=".65" begin="{begin:.2f}s"/>'
        f'<set attributeName="opacity" to="0" begin="{begin + duration:.2f}s"/>'
        "</rect>"
    )
    return clip, cursor


def preview_note(summary: Summary, y: float) -> str:
    if not summary.preview:
        return ""
    return label(
        WIDTH,
        y,
        "preview data · workflow replaces this after its first run",
        10,
        "dim-fill",
        "end",
    )


def draw_stats(summary: Summary) -> str:
    height = 150
    weekly = summary.weekly or (0,)
    peak = max(weekly, default=1) or 1
    parts = [svg_open(height)]

    parts.append(
        f'<g opacity="0">{fade(0.08)}'
        + label(0, 50, summary.total, 50, "emph-fill", weight=600)
        + label(0, 72, "public contributions in the last year", 12)
        + "</g>"
    )
    parts.append(
        f'<g opacity="0">{fade(0.24)}'
        + label(WIDTH, 30, summary.active_days, 19, "emph-fill", "end", 600)
        + label(WIDTH, 47, "active days", 11, "dim-fill", "end")
        + label(WIDTH, 70, summary.best_week, 19, "emph-fill", "end", 600)
        + label(WIDTH, 87, "best week", 11, "dim-fill", "end")
        + "</g>"
    )
    parts.append(preview_note(summary, 98))

    top = 108
    baseline = 140
    step = WIDTH / max(len(weekly) - 1, 1)
    points = [
        (index * step, baseline - (value / peak) * (baseline - top))
        for index, value in enumerate(weekly)
    ]
    clip, cursor = reveal_clip("stats-reveal", 0, top - 5, WIDTH, baseline - top + 8)
    parts.append(clip)
    line_path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area_path = (
        f"M{points[0][0]:.1f} {baseline:.1f} "
        + " ".join(f"L{x:.1f} {y:.1f}" for x, y in points)
        + f" L{points[-1][0]:.1f} {baseline:.1f} Z"
    )
    parts.append(
        '<g clip-path="url(#stats-reveal)">'
        f'<path d="{area_path}" class="area-fill"/>'
        f'<path d="{line_path}" class="data-stroke" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        "</g>"
    )
    parts.append(cursor)
    parts.append("</svg>")
    return "".join(parts)


def pretty_date(value: str | None) -> str:
    if not value:
        return "—"
    parsed = date.fromisoformat(value)
    return f"{MONTHS[parsed.month - 1]} {parsed.day}"


def streak_span(streak: Streak) -> str:
    if not streak.start or not streak.end:
        return "—"
    return f"{pretty_date(streak.start)} – {pretty_date(streak.end)}"


def draw_streak(summary: Summary) -> str:
    height = 104
    midpoint = WIDTH / 2
    parts = [svg_open(height)]
    parts.append(
        f'<line x1="{midpoint:.1f}" y1="14" x2="{midpoint:.1f}" y2="88" '
        f'class="rule-stroke" stroke-width="1" opacity="0">{fade(0.18)}</line>'
    )
    for index, (streak, title) in enumerate((
        (summary.current, "current streak"),
        (summary.longest, "longest streak"),
    )):
        left = 0 if index == 0 else midpoint + 28
        anchor_x = left
        parts.append(
            f'<g opacity="0">{fade(0.22 + index * 0.12)}'
            + label(anchor_x, 42, streak.length, 30, "emph-fill", weight=600)
            + label(anchor_x, 61, title, 11)
            + label(anchor_x, 80, streak_span(streak), 10)
            + "</g>"
        )
    parts.append("</svg>")
    return "".join(parts)


def horizontal_bar(x: float, y: float, width: float, height: float) -> str:
    if width <= 0:
        return ""
    radius = min(3.0, height / 2, width)
    return (
        f'<path d="M{x:.1f} {y:.1f} H{x + width - radius:.1f} '
        f'Q{x + width:.1f} {y:.1f} {x + width:.1f} {y + radius:.1f} '
        f'V{y + height - radius:.1f} '
        f'Q{x + width:.1f} {y + height:.1f} {x + width - radius:.1f} {y + height:.1f} '
        f'H{x:.1f} Z" class="data-fill"/>'
    )


def draw_language_column(
    title: str,
    values: tuple[tuple[str, int], ...],
    x: float,
    width: float,
    formatter,
) -> str:
    parts = [label(x, 19, title, 11, "dim-fill")]
    peak = max((value for _, value in values), default=1)
    bar_start = x + 104
    bar_width = width - 104
    for index, (name, value) in enumerate(values):
        y = 40 + index * 27
        parts.append(label(x, y + 8, name, 10, "emph-fill"))
        parts.append(
            horizontal_bar(
                bar_start,
                y,
                max(2, (value / peak) * (bar_width - 42)),
                9,
            )
        )
        parts.append(label(x + width, y + 8, formatter(value), 9, "dim-fill", "end"))
    return "".join(parts)


def compact_bytes(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def draw_languages(summary: Summary) -> str:
    height = 177
    gap = 26
    column_width = (WIDTH - gap) / 2
    parts = [svg_open(height)]
    clip, cursor = reveal_clip("language-reveal", 0, 0, WIDTH, height - 12, 0.22, 1.35)
    parts.append(clip)
    parts.append('<g clip-path="url(#language-reveal)">')
    parts.append(draw_language_column(
        "top languages by bytes",
        summary.languages_by_size,
        0,
        column_width,
        compact_bytes,
    ))
    parts.append(draw_language_column(
        "primary language by repository",
        summary.languages_by_repo,
        column_width + gap,
        column_width,
        lambda value: str(value),
    ))
    parts.append("</g>")
    parts.append(cursor)
    parts.append("</svg>")
    return "".join(parts)


def contribution_level(count: int, peak: int) -> int:
    if count <= 0 or peak <= 0:
        return 0
    return min(4, max(1, math.ceil((count / peak) * 4)))


def draw_year(summary: Summary) -> str:
    height = 142
    days = list(summary.days)
    if not days:
        return svg_open(height) + label(0, 70, "No public contribution data.", 12) + "</svg>"

    start = date.fromisoformat(days[0]["date"])
    leading = (start.weekday() + 1) % 7
    grid: list[dict[str, Any] | None] = [None] * leading + days
    weeks = math.ceil(len(grid) / 7)
    while len(grid) < weeks * 7:
        grid.append(None)

    peak = max((day["contributionCount"] for day in days), default=0)
    left = 34
    top = 25
    cell_x = 10.8
    cell_y = 15.0
    parts = [svg_open(height)]
    parts.append(label(0, top + cell_y * 2 + 4, "tue", 9))
    parts.append(label(0, top + cell_y * 4 + 4, "thu", 9))
    parts.append(label(0, top + cell_y * 6 + 4, "sat", 9))

    month_positions: dict[int, int] = {}
    for index, day in enumerate(grid):
        if not day:
            continue
        parsed = date.fromisoformat(day["date"])
        week_index = index // 7
        month_positions.setdefault(parsed.month, week_index)

    last_label_x = -100.0
    for month, week_index in sorted(month_positions.items(), key=lambda item: item[1]):
        x = left + week_index * cell_x
        if x - last_label_x >= 34:
            parts.append(label(x, 11, MONTHS[month - 1], 9))
            last_label_x = x

    clip, cursor = reveal_clip(
        "year-reveal",
        left,
        top - 5,
        min(WIDTH - left, weeks * cell_x + 4),
        7 * cell_y + 8,
        0.20,
        1.55,
    )
    parts.append(clip)
    parts.append('<g clip-path="url(#year-reveal)">')
    for index, day in enumerate(grid):
        if not day:
            continue
        week_index = index // 7
        weekday = index % 7
        x = left + week_index * cell_x
        y = top + weekday * cell_y
        level = contribution_level(int(day["contributionCount"]), peak)
        character = RAMP[level]
        class_name = "dim-fill" if level == 0 else "data-fill"
        parts.append(label(x, y + 10, character or "·", 12, class_name))
    parts.append("</g>")
    parts.append(cursor)
    parts.append("</svg>")
    return "".join(parts)


def draw_heading(text: str) -> str:
    height = 28
    escaped = html.escape(text)
    line_start = min(170, max(70, 14 + len(text) * 10))
    return (
        svg_open(height)
        + f'<text x="0" y="19" class="emph-fill" font-size="16" '
          f'font-weight="600">{escaped}</text>'
        + f'<line x1="{line_start}" y1="13.5" x2="{WIDTH}" y2="13.5" '
          'class="rule-stroke" stroke-width="1"/>'
        + "</svg>"
    )


def write_outputs(summary: Summary, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "stats.svg": draw_stats(summary),
        "streak.svg": draw_streak(summary),
        "langs.svg": draw_languages(summary),
        "year.svg": draw_year(summary),
        "hd-about.svg": draw_heading("about"),
        "hd-stack.svg": draw_heading("stack"),
        "hd-projects.svg": draw_heading("selected projects"),
        "hd-stats.svg": draw_heading("public GitHub activity"),
        "hd-about-this-page.svg": draw_heading("about this page"),
    }
    for filename, content in outputs.items():
        (out_dir / filename).write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", default=os.getenv("GH_LOGIN", "Chin-Wee"))
    parser.add_argument("--out-dir", default=os.getenv("OUT_DIR", "."))
    parser.add_argument("--demo", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.demo:
        profile = demo_profile()
    else:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise SystemExit("GITHUB_TOKEN is required unless --demo is used")
        profile = fetch_profile(args.login, token)

    write_outputs(summarise(profile), Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
