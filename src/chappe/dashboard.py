"""Render a shareable PNG dashboard summarizing a Telegram channel.

The card is a magazine-cover poster, not a dashboard: Chappie as the hero,
the channel's strongest post as a pull-quote, and a single ledger strip with
the supporting numbers. Designed to read at thumbnail size and be the kind of
image a channel owner actually wants to post.
"""
from __future__ import annotations

import random
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dependency declared in pyproject
    Image = None  # type: ignore[assignment]


CANVAS_W = 1080
CANVAS_H = 1350

# Three-ink letterpress palette
PAPER_CREAM = (244, 234, 213)
PAPER_SHADE = (228, 215, 188)
INK_GREEN = (38, 70, 44)
INK_SEPIA = (107, 70, 38)
INK_FAINT = (170, 150, 118)


FONT_CANDIDATES: dict[str, list[tuple[str, int]]] = {
    # True upright bold serif for mastheads (no italic slant)
    "display_bold": [
        ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 2),
        ("/System/Library/Fonts/Supplemental/Didot.ttc", 2),
        ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
    ],
    # Display serif for big numbers (heavier weight, slight slant tolerable)
    "serif_black": [
        ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 2),
        ("/System/Library/Fonts/Supplemental/Hoefler Text.ttc", 2),
        ("/System/Library/Fonts/Supplemental/Didot.ttc", 2),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
    ],
    "serif_regular": [
        ("/System/Library/Fonts/Supplemental/Hoefler Text.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Georgia.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 0),
    ],
    "serif_italic": [
        ("/System/Library/Fonts/Supplemental/Hoefler Text.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 1),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf", 0),
    ],
    "sans_bold": [
        ("/System/Library/Fonts/Avenir.ttc", 4),
        ("/System/Library/Fonts/Avenir Next.ttc", 1),
        ("/System/Library/Fonts/Optima.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ],
    "sans_regular": [
        ("/System/Library/Fonts/Avenir.ttc", 0),
        ("/System/Library/Fonts/Avenir Next.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    "mono": [
        ("/System/Library/Fonts/Menlo.ttc", 0),
        ("/System/Library/Fonts/Monaco.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
    ],
}


MASCOT_POOL = (
    "chappie-recorder.png",
    "chappie-signal-operator.png",
    "chappie-lookout.png",
    "chappie-scout-map-reader.png",
    "chappie-night-watch.png",
)


_ROMAN_NUMERALS = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def _roman(n: int) -> str:
    if n <= 0:
        return "I"
    out: list[str] = []
    for value, sym in _ROMAN_NUMERALS:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def _load_font(role: str, size: int) -> Any:
    for path, idx in FONT_CANDIDATES.get(role, []):
        try:
            return ImageFont.truetype(path, size, index=idx)
        except (OSError, IndexError):
            continue
    return ImageFont.load_default()


def _strip_leading_emoji(text: str) -> str:
    """Pillow can't render color emoji; drop leading symbol glyphs + whitespace."""
    i = 0
    while i < len(text):
        ch = text[i]
        cat = unicodedata.category(ch)
        if cat in {"So", "Sk", "Sm", "Cn"} or ord(ch) >= 0x1F000:
            i += 1
            continue
        if ch.isspace() and i < 10:
            i += 1
            continue
        break
    return text[i:] if i else text


def _truncate(text: str, max_chars: int) -> str:
    text = _strip_leading_emoji((text or "").replace("\n", " ").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _best_question(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the most engaging question from the comment store.

    Order of preference: highest-reacted ? -> longest ? -> first ?. Filters
    out trivial questions (<20 chars) so we never land on 'только ру карты?'
    when there are 920 better candidates."""
    questions = [c for c in comments if "?" in (c.get("text") or "")]
    if not questions:
        return None

    substantive = [
        c for c in questions if len(_strip_leading_emoji(c.get("text") or "")) >= 30
    ]
    pool = substantive if substantive else questions

    def score(c: dict[str, Any]) -> tuple[int, int]:
        reactions = int(c.get("reactions") or 0)
        return (reactions, len(c.get("text") or ""))

    pool.sort(key=score, reverse=True)
    return pool[0]


def _format_lift(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate posts by media type and compute (count, mean forward rate)."""
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        by_type[(post.get("media_type") or "text")].append(post)
    rows: list[dict[str, Any]] = []
    for fmt, members in by_type.items():
        views = sum(int(p.get("views") or 0) for p in members)
        forwards = sum(int(p.get("forwards") or 0) for p in members)
        rows.append(
            {
                "format": fmt,
                "count": len(members),
                "fwd_rate": (forwards / views) if views else 0.0,
                "forwards_total": forwards,
            }
        )
    rows.sort(key=lambda r: r["fwd_rate"], reverse=True)
    most_used = max(rows, key=lambda r: r["count"]) if rows else None
    best_lift = rows[0] if rows else None
    return {
        "by_format": rows,
        "most_used": most_used,
        "best_lift": best_lift,
    }


def compute_dashboard_stats(
    posts: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    total_views = sum(int(p.get("views") or 0) for p in posts)
    total_forwards = sum(int(p.get("forwards") or 0) for p in posts)
    mean_fwd_rate = (total_forwards / total_views) if total_views else 0.0
    peak_views = max((int(p.get("views") or 0) for p in posts), default=0)
    peak_forwards = max((int(p.get("forwards") or 0) for p in posts), default=0)

    fmt = _format_lift(posts)
    most_used_fmt = fmt["most_used"]["format"] if fmt["most_used"] else "text"
    best_lift_fmt = fmt["best_lift"]["format"] if fmt["best_lift"] else "text"
    best_lift_rate = fmt["best_lift"]["fwd_rate"] if fmt["best_lift"] else 0.0

    ranked = sorted(
        posts,
        key=lambda p: int(p.get("forwards") or 0),
        reverse=True,
    )[:3]
    top_posts: list[dict[str, Any]] = []
    for post in ranked:
        text = _strip_leading_emoji((post.get("text") or "").replace("\n", " ").strip())
        if not text:
            text = f"(media-only · {post.get('media_type') or 'post'})"
        top_posts.append(
            {
                "id": post.get("id"),
                "link": post.get("link"),
                "text": text,
                "views": int(post.get("views") or 0),
                "forwards": int(post.get("forwards") or 0),
                "media_type": post.get("media_type") or "text",
            }
        )

    question = _best_question(comments)

    return {
        "posts": len(posts),
        "comments": len(comments),
        "mean_forward_rate": round(mean_fwd_rate, 6),
        "total_forwards": int(total_forwards),
        "peak_views": int(peak_views),
        "peak_forwards": int(peak_forwards),
        "most_used_format": most_used_fmt,
        "best_format_by_lift": best_lift_fmt,
        "best_format_lift_rate": round(best_lift_rate, 6),
        "format_breakdown": {row["format"]: row for row in fmt["by_format"]},
        "top_posts": top_posts,
        "sample_question": question["text"] if question else None,
        "sample_question_reactions": int(question.get("reactions") or 0) if question else 0,
        "questions_count": sum(1 for c in comments if "?" in (c.get("text") or "")),
    }


def _paper_background(width: int, height: int, seed: int = 42) -> Any:
    img = Image.new("RGB", (width, height), PAPER_CREAM)
    draw = ImageDraw.Draw(img)
    rng = random.Random(seed)
    # Sparse fiber speckle, ~2.5% of pixels
    speckles = (width * height) // 420
    for _ in range(speckles):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        roll = rng.random()
        if roll < 0.55:
            draw.point((x, y), fill=PAPER_SHADE)
        elif roll < 0.88:
            draw.point((x, y), fill=INK_FAINT)
        else:
            draw.point((x, y), fill=INK_SEPIA)
    # Subtle corner vignette
    for cx, cy in [(0, 0), (width, 0), (0, height), (width, height)]:
        for _ in range(900):
            dx = rng.gauss(0, 90)
            dy = rng.gauss(0, 90)
            px = int(cx + dx)
            py = int(cy + dy)
            if 0 <= px < width and 0 <= py < height:
                draw.point((px, py), fill=PAPER_SHADE)
    return img


def _double_rule(draw: Any, x1: int, y: int, x2: int, *, gap: int = 5, color=INK_GREEN):
    draw.line([(x1, y), (x2, y)], fill=color, width=3)
    draw.line([(x1, y + gap + 3), (x2, y + gap + 3)], fill=color, width=1)


def _centered_text(
    draw: Any,
    text: str,
    *,
    y: int,
    font: Any,
    fill: tuple[int, int, int],
    canvas_w: int = CANVAS_W,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = (canvas_w - w) // 2 - bbox[0]
    draw.text((x, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def _wrap_text(text: str, *, font: Any, max_width: int, draw: Any) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _asset_path(name: str) -> Path | None:
    here = Path(__file__).parent / "assets" / name
    if here.exists():
        return here
    return None


def _pick_mascot(seed: int) -> Path | None:
    rng = random.Random(seed)
    pool = list(MASCOT_POOL)
    rng.shuffle(pool)
    for name in pool:
        path = _asset_path(name)
        if path:
            return path
    return None


def _load_mascot_keyed(path: Path, size: int) -> Any:
    """Open the mascot, alpha-key the near-white background so it sits on paper.

    The illustrations come with an off-white sky background that reads as a
    hard rectangle when composited on cream paper. We replace pixels above a
    'whiteness' threshold with full transparency and fade pixels near the
    threshold so the silhouette edge stays soft.
    """
    mascot = Image.open(path).convert("RGBA")
    mascot = mascot.resize((size, size), Image.LANCZOS)
    px = mascot.load()
    w, h = mascot.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            brightness = (r + g + b) / 3
            warm_white = r > 232 and g > 226 and b > 205
            if warm_white and brightness > 235:
                px[x, y] = (r, g, b, 0)
            elif warm_white and brightness > 215:
                # Soft edge fade
                fade = (brightness - 215) / 20
                new_alpha = int(a * (1 - fade))
                px[x, y] = (r, g, b, max(0, new_alpha))
    return mascot


def _spaced_caps(text: str, *, letter_space: int = 1) -> str:
    if letter_space <= 0:
        return text.upper()
    return (" " * letter_space).join(list(text.upper()))


_PERIOD_LABELS_EN = {
    "7d": "Seven days",
    "14d": "Fourteen days",
    "30d": "Thirty days",
    "60d": "Sixty days",
    "90d": "Ninety days",
    "180d": "One hundred eighty days",
    "365d": "Three hundred sixty-five days",
}

_PERIOD_LABELS_RU = {
    "7d": "Семь дней",
    "14d": "Четырнадцать дней",
    "30d": "Тридцать дней",
    "60d": "Шестьдесят дней",
    "90d": "Девяносто дней",
    "180d": "Сто восемьдесят дней",
    "365d": "Триста шестьдесят пять дней",
}


def render_dashboard_png(
    channel: str,
    period_label: str,
    stats: dict[str, Any],
    output_path: Path,
    *,
    lang: str = "en",
    edition: int | None = None,
    rendered_on: str | None = None,
) -> Path:
    """Render the channel poster. Layout is intentionally sparse:

    1. Thin masthead rule with edition stamp
    2. Mascot hero (~520 px) on the left, channel block on the right
    3. Pull-quote from the channel's #1 post by forwards
    4. One feature stat: forwards on that post + the channel's mean fwd-rate
    5. Ledger strip with supporting numbers
    6. Footer attribution
    """
    if Image is None:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for chappe wrapped; install with `pip install Pillow`."
        )

    seed = sum(ord(c) for c in channel) + len(channel) * 17
    img = _paper_background(CANVAS_W, CANVAS_H, seed=seed)
    draw = ImageDraw.Draw(img)

    margin = 80
    inner_w = CANVAS_W - margin * 2

    # ── 1. Masthead ────────────────────────────────────────────────────
    masthead_y = 60
    edition_num = edition if edition is not None else (seed % 89) + 11
    masthead = f"THE CHAPPE — WRAPPED   ·   N°  {_roman(edition_num)}"
    _centered_text(
        draw,
        _spaced_caps(masthead, letter_space=1),
        y=masthead_y,
        font=_load_font("sans_bold", 19),
        fill=INK_GREEN,
    )
    rule_y = masthead_y + 36
    draw.line([(margin, rule_y), (CANVAS_W - margin, rule_y)], fill=INK_GREEN, width=2)

    # ── 2. Hero row: mascot left, channel block right ───────────────────
    hero_top = rule_y + 50
    mascot_path = _pick_mascot(seed)
    mascot_size = 540
    mascot_drawn_w = 0
    if mascot_path:
        mascot = _load_mascot_keyed(mascot_path, mascot_size)
        mx = margin - 30
        my = hero_top - 20
        img.paste(mascot, (mx, my), mascot)
        mascot_drawn_w = mascot_size - 30
    text_x = margin + mascot_drawn_w + 20

    # Channel block — right-anchored vertical centering with mascot
    block_top = hero_top + 60
    draw.text(
        (text_x, block_top),
        _spaced_caps("Channel dispatch", letter_space=3),
        font=_load_font("sans_bold", 17),
        fill=INK_SEPIA,
    )
    handle_text = channel
    handle_size = 60
    handle_font = _load_font("display_bold", handle_size)
    handle_bbox = draw.textbbox((0, 0), handle_text, font=handle_font)
    while (handle_bbox[2] - handle_bbox[0]) > (CANVAS_W - margin - text_x) and handle_size > 36:
        handle_size -= 4
        handle_font = _load_font("display_bold", handle_size)
        handle_bbox = draw.textbbox((0, 0), handle_text, font=handle_font)
    handle_y = block_top + 38
    draw.text(
        (text_x, handle_y),
        handle_text,
        font=handle_font,
        fill=INK_GREEN,
    )
    period_word = (_PERIOD_LABELS_RU if lang == "ru" else _PERIOD_LABELS_EN).get(
        period_label, period_label
    )
    if lang == "ru":
        subtitle = f"обзор за {period_word.lower()}"
    else:
        subtitle = f"a survey of {period_word.lower()}"
    if rendered_on:
        subtitle += f"  ·  {rendered_on}"
    subtitle_y = handle_y + (handle_bbox[3] - handle_bbox[1]) + 28
    draw.text(
        (text_x, subtitle_y),
        subtitle,
        font=_load_font("serif_italic", 24),
        fill=INK_SEPIA,
    )
    # Decorative thin rule under the channel block
    rule_under_y = subtitle_y + 50
    draw.line(
        [(text_x, rule_under_y), (CANVAS_W - margin, rule_under_y)],
        fill=INK_FAINT,
        width=1,
    )

    # ── 3. Pull-quote from #1 post ───────────────────────────────────
    # Place the quote BELOW the mascot, full width, with proper hierarchy
    quote_top = hero_top + mascot_size - 40
    top_post = stats["top_posts"][0] if stats["top_posts"] else None
    if top_post:
        # Quote text in REGULAR serif (not bold) so it sits below the channel name
        quote_font = _load_font("serif_regular", 34)
        raw = _truncate(top_post["text"], 160)
        wrapped = _wrap_text(raw, font=quote_font, max_width=inner_w - 90, draw=draw)[:3]
        # Open quote ornament — large but in faint sepia so it decorates not dominates
        oqf = _load_font("serif_black", 110)
        draw.text(
            (margin - 8, quote_top - 30),
            "“",
            font=oqf,
            fill=INK_SEPIA,
        )
        line_y = quote_top + 28
        line_height = 46
        last_line_y = line_y
        for line in wrapped:
            draw.text(
                (margin + 60, line_y),
                line,
                font=quote_font,
                fill=INK_GREEN,
            )
            last_line_y = line_y
            line_y += line_height
        # Smaller closing quote, placed at the end of the last text line
        cqf = _load_font("serif_black", 60)
        last_line = wrapped[-1] if wrapped else ""
        lb = draw.textbbox((0, 0), last_line, font=quote_font)
        cq_x = margin + 60 + (lb[2] - lb[0]) + 8
        draw.text(
            (min(cq_x, CANVAS_W - margin - 40), last_line_y - 20),
            "”",
            font=cqf,
            fill=INK_SEPIA,
        )
        quote_bottom = line_y + 8
    else:
        quote_bottom = quote_top + 80

    # ── 4. Feature stat strip ──────────────────────────────────────────
    feature_y = quote_bottom + 28
    # Left: top post forwards
    if top_post:
        big_num = f"{top_post['forwards']:,}"
        num_font = _load_font("serif_black", 96)
        nb = draw.textbbox((0, 0), big_num, font=num_font)
        draw.text((margin, feature_y - nb[1]), big_num, font=num_font, fill=INK_GREEN)
        label_lift = "FORWARDS  ·  TOP DISPATCH" if lang == "en" else "ФОРВАРДОВ  ·  ЛУЧШИЙ ПОСТ"
        draw.text(
            (margin, feature_y + (nb[3] - nb[1]) + 6),
            _spaced_caps(label_lift, letter_space=1),
            font=_load_font("sans_bold", 15),
            fill=INK_SEPIA,
        )
    # Right: mean forward rate
    rate_text = f"{stats['mean_forward_rate'] * 100:.2f}%"
    rate_font = _load_font("serif_black", 96)
    rb = draw.textbbox((0, 0), rate_text, font=rate_font)
    rw = rb[2] - rb[0]
    rx = CANVAS_W - margin - rw
    draw.text((rx, feature_y - rb[1]), rate_text, font=rate_font, fill=INK_GREEN)
    label_rate = "MEAN FORWARD RATE" if lang == "en" else "СРЕД. ФОРВАРД-РЕЙТ"
    rate_label_bbox = draw.textbbox(
        (0, 0),
        _spaced_caps(label_rate, letter_space=1),
        font=_load_font("sans_bold", 15),
    )
    rate_label_w = rate_label_bbox[2] - rate_label_bbox[0]
    draw.text(
        (CANVAS_W - margin - rate_label_w, feature_y + (rb[3] - rb[1]) + 6),
        _spaced_caps(label_rate, letter_space=1),
        font=_load_font("sans_bold", 15),
        fill=INK_SEPIA,
    )

    # ── 5. Ledger strip ──────────────────────────────────────────────
    ledger_y = feature_y + 130
    draw.line([(margin, ledger_y), (CANVAS_W - margin, ledger_y)], fill=INK_GREEN, width=2)
    draw.line(
        [(margin, ledger_y + 5), (CANVAS_W - margin, ledger_y + 5)],
        fill=INK_GREEN,
        width=1,
    )

    ledger_text_y = ledger_y + 22
    fact_font = _load_font("sans_bold", 20)
    fact_value_font = _load_font("serif_black", 28)
    # 4 small facts evenly distributed
    def _fmt_compact(n: int) -> str:
        if n >= 100000:
            return f"{n / 1000:.0f}K"
        if n >= 10000:
            return f"{n / 1000:.1f}K"
        return f"{n:,}"

    facts: list[tuple[str, str]] = [
        (f"{stats['posts']:,}", "POSTS" if lang == "en" else "ПОСТОВ"),
        (f"{stats['comments']:,}", "COMMENTS" if lang == "en" else "КОММЕНТ."),
        (
            f"{stats.get('questions_count', 0):,}",
            "QUESTIONS" if lang == "en" else "ВОПРОСОВ",
        ),
        (
            _fmt_compact(int(stats.get("peak_views") or 0)) or "0",
            "PEAK VIEWS" if lang == "en" else "ПИК ПРОСМ.",
        ),
    ]
    fact_w = inner_w // 4
    for i, (val, label) in enumerate(facts):
        col_x = margin + i * fact_w + fact_w // 2
        # value (display serif)
        vb = draw.textbbox((0, 0), val, font=fact_value_font)
        draw.text(
            (col_x - (vb[2] - vb[0]) // 2 - vb[0], ledger_text_y),
            val,
            font=fact_value_font,
            fill=INK_GREEN,
        )
        # label
        lb = draw.textbbox((0, 0), _spaced_caps(label, letter_space=2), font=fact_font)
        draw.text(
            (col_x - (lb[2] - lb[0]) // 2 - lb[0], ledger_text_y + (vb[3] - vb[1]) + 12),
            _spaced_caps(label, letter_space=2),
            font=_load_font("sans_bold", 14),
            fill=INK_SEPIA,
        )
        if i < 3:
            sep_x = margin + (i + 1) * fact_w
            draw.line(
                [(sep_x, ledger_text_y + 6), (sep_x, ledger_text_y + 70)],
                fill=INK_FAINT,
                width=1,
            )

    ledger_bot = ledger_text_y + 95
    draw.line([(margin, ledger_bot), (CANVAS_W - margin, ledger_bot)], fill=INK_GREEN, width=1)
    draw.line(
        [(margin, ledger_bot + 5), (CANVAS_W - margin, ledger_bot + 5)],
        fill=INK_GREEN,
        width=2,
    )

    # ── 6. Footer (single line, centered) ──────────────────────────────
    footer_y = CANVAS_H - 78
    footer_text = "POSTED BY CHAPPIE,  THE LITTLE TOWER KEEPER"
    if lang == "ru":
        footer_text = "ОТПРАВЛЕНО ЧАППИ,  ХРАНИТЕЛЕМ БАШНИ"
    _centered_text(
        draw,
        _spaced_caps(footer_text, letter_space=2),
        y=footer_y,
        font=_load_font("sans_bold", 14),
        fill=INK_SEPIA,
    )
    _centered_text(
        draw,
        "github.com/crimeacs/chappe",
        y=footer_y + 28,
        font=_load_font("mono", 22),
        fill=INK_GREEN,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def render_caption(
    channel: str,
    period_label: str,
    stats: dict[str, Any],
    *,
    lang: str = "en",
) -> str:
    """Caption now carries the data that we pulled off the image."""
    top_lines = []
    for idx, post in enumerate(stats["top_posts"], 1):
        text = _truncate(post.get("text") or "", 60)
        link = post.get("link") or ""
        top_lines.append(f"#{idx} {text} — {post['forwards']:,} fwd · {link}")
    top_block = "\n".join(top_lines)
    fwd_rate = f"{stats['mean_forward_rate'] * 100:.2f}%"
    lift = stats.get("best_format_by_lift") or stats.get("most_used_format") or "text"
    lift_rate = f"{stats.get('best_format_lift_rate', 0) * 100:.2f}%"
    most_used = stats.get("most_used_format") or "text"
    question = stats.get("sample_question") or ("—" if lang == "en" else "—")
    questions_count = stats.get("questions_count", 0)

    if lang == "ru":
        return (
            f"📡 The Chappe-Wrapped: {channel} · {period_label}\n\n"
            f"{stats['posts']:,} постов · {stats['comments']:,} комментариев · "
            f"{questions_count:,} вопросов в комментах\n"
            f"Mean forward-rate: {fwd_rate}\n"
            f"Чаще всего: {most_used} · Лучше форвардится: {lift} ({lift_rate})\n\n"
            f"Топ-3 по форвардам:\n{top_block}\n\n"
            f"Один из самых обсуждаемых вопросов:\n«{question}»\n\n"
            f"Сгенерировано Чаппи — хранителем башни.\n"
            f"github.com/crimeacs/chappe"
        )
    return (
        f"📡 The Chappe-Wrapped: {channel} · {period_label}\n\n"
        f"{stats['posts']:,} posts · {stats['comments']:,} comments · "
        f"{questions_count:,} questions raised\n"
        f"Mean forward-rate: {fwd_rate}\n"
        f"Most used: {most_used} · Best forward lift: {lift} ({lift_rate})\n\n"
        f"Top 3 by forwards:\n{top_block}\n\n"
        f"One of the most engaging questions readers asked:\n“{question}”\n\n"
        f"Posted by Chappie — the little tower keeper.\n"
        f"github.com/crimeacs/chappe"
    )
