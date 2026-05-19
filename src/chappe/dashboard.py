"""Render a shareable PNG dashboard summarizing a Telegram channel.

The image is a vertical 1080x1350 card so it fits the Telegram preview and the
common social aspect ratios. The renderer takes a stats dict that the CLI
assembles from the local store and writes a PNG plus a caption template.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dependency declared in pyproject
    Image = None  # type: ignore[assignment]


CANVAS_W = 1080
CANVAS_H = 1350

BG_COLOR = (16, 22, 30)
CARD_COLOR = (28, 36, 48)
CARD_BORDER = (44, 56, 76)
TEXT_PRIMARY = (245, 248, 252)
TEXT_SECONDARY = (170, 184, 204)
TEXT_TERTIARY = (118, 132, 154)
ACCENT = (107, 196, 255)
ACCENT_GOLD = (255, 196, 92)
ACCENT_GREEN = (132, 220, 168)


FONT_PATHS_REGULAR = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]
FONT_PATHS_BOLD = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _load_font(role: str, size: int) -> Any:
    candidates = FONT_PATHS_BOLD if role == "bold" else FONT_PATHS_REGULAR
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def compute_dashboard_stats(
    posts: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce raw posts+comments into the numbers the dashboard renders."""
    total_views = sum(int(p.get("views") or 0) for p in posts)
    total_forwards = sum(int(p.get("forwards") or 0) for p in posts)
    mean_fwd_rate = (total_forwards / total_views) if total_views else 0.0
    formats = Counter((p.get("media_type") or "text") for p in posts)
    best_format = formats.most_common(1)[0][0] if formats else "text"
    ranked = sorted(
        posts,
        key=lambda p: int(p.get("forwards") or 0),
        reverse=True,
    )[:3]
    top_posts: list[dict[str, Any]] = []
    for post in ranked:
        text = (post.get("text") or "").replace("\n", " ").strip()
        if not text:
            text = f"(media-only · {post.get('media_type') or 'post'})"
        top_posts.append(
            {
                "id": post.get("id"),
                "link": post.get("link"),
                "text": text,
                "views": int(post.get("views") or 0),
                "forwards": int(post.get("forwards") or 0),
            }
        )
    questions = [c for c in comments if "?" in (c.get("text") or "")]
    sample_question = questions[0]["text"] if questions else None
    return {
        "posts": len(posts),
        "comments": len(comments),
        "mean_forward_rate": round(mean_fwd_rate, 6),
        "best_format": best_format,
        "format_breakdown": dict(formats),
        "top_posts": top_posts,
        "sample_question": sample_question,
        "questions_count": len(questions),
    }


def render_dashboard_png(
    channel: str,
    period_label: str,
    stats: dict[str, Any],
    output_path: Path,
) -> Path:
    if Image is None:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for chappe wrapped; install with `pip install Pillow`."
        )

    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Header band
    draw.rectangle([(0, 0), (CANVAS_W, 132)], fill=CARD_COLOR)
    draw.text((48, 28), "chappe-wrapped", font=_load_font("bold", 28), fill=ACCENT)
    draw.text(
        (48, 70),
        f"{channel} · {period_label}",
        font=_load_font("bold", 38),
        fill=TEXT_PRIMARY,
    )

    # Big stats cards
    cards = [
        (f"{stats['posts']:,}", "posts"),
        (f"{stats['comments']:,}", "comments"),
        (f"{stats['mean_forward_rate'] * 100:.2f}%", "fwd / views"),
    ]
    card_w = (CANVAS_W - 48 * 2 - 32 * 2) // 3
    y_top = 180
    for i, (big, label) in enumerate(cards):
        x = 48 + i * (card_w + 32)
        draw.rounded_rectangle(
            [(x, y_top), (x + card_w, y_top + 200)],
            radius=22,
            fill=CARD_COLOR,
            outline=CARD_BORDER,
            width=2,
        )
        draw.text((x + 24, y_top + 36), big, font=_load_font("bold", 64), fill=ACCENT)
        draw.text(
            (x + 24, y_top + 130),
            label,
            font=_load_font("regular", 26),
            fill=TEXT_SECONDARY,
        )

    # Top posts list
    section_y = 430
    draw.text(
        (48, section_y),
        "TOP POSTS BY FORWARDS",
        font=_load_font("bold", 24),
        fill=TEXT_TERTIARY,
    )
    row_y = section_y + 46
    for idx, post in enumerate(stats["top_posts"], 1):
        draw.rounded_rectangle(
            [(48, row_y), (CANVAS_W - 48, row_y + 130)],
            radius=18,
            fill=CARD_COLOR,
            outline=CARD_BORDER,
            width=2,
        )
        draw.text(
            (68, row_y + 24),
            f"#{idx}",
            font=_load_font("bold", 40),
            fill=ACCENT_GOLD,
        )
        text_x = 156
        text = _truncate(post.get("text") or "", 70)
        draw.text(
            (text_x, row_y + 22),
            text,
            font=_load_font("regular", 24),
            fill=TEXT_PRIMARY,
        )
        meta = f"{post['views']:,} views   •   {post['forwards']:,} forwards"
        draw.text(
            (text_x, row_y + 72),
            meta,
            font=_load_font("regular", 22),
            fill=TEXT_SECONDARY,
        )
        row_y += 144

    # Footer "best format + audience asks" combo
    combo_y = row_y + 20
    draw.rounded_rectangle(
        [(48, combo_y), (CANVAS_W - 48, combo_y + 170)],
        radius=20,
        fill=CARD_COLOR,
        outline=CARD_BORDER,
        width=2,
    )
    col_x = 70
    draw.text(
        (col_x, combo_y + 26),
        "BEST FORMAT",
        font=_load_font("bold", 22),
        fill=TEXT_TERTIARY,
    )
    draw.text(
        (col_x, combo_y + 62),
        stats["best_format"].upper(),
        font=_load_font("bold", 44),
        fill=ACCENT_GREEN,
    )

    col2_x = CANVAS_W // 2 + 20
    draw.text(
        (col2_x, combo_y + 26),
        "AUDIENCE ASKS",
        font=_load_font("bold", 22),
        fill=TEXT_TERTIARY,
    )
    question = stats["sample_question"]
    if question:
        question_lines = _wrap_text(
            _truncate(question, 96),
            font=_load_font("regular", 22),
            max_width=(CANVAS_W - 48 - col2_x - 20),
            draw=draw,
        )
        line_y = combo_y + 62
        for line in question_lines[:3]:
            draw.text((col2_x, line_y), line, font=_load_font("regular", 22), fill=TEXT_PRIMARY)
            line_y += 30
    else:
        draw.text(
            (col2_x, combo_y + 62),
            "no questions found",
            font=_load_font("regular", 22),
            fill=TEXT_TERTIARY,
        )

    # Bottom credit
    bottom_y = CANVAS_H - 96
    draw.text(
        (48, bottom_y),
        "generated by chappe",
        font=_load_font("regular", 24),
        fill=TEXT_SECONDARY,
    )
    draw.text(
        (48, bottom_y + 36),
        "github.com/crimeacs/chappe",
        font=_load_font("bold", 22),
        fill=ACCENT,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def _wrap_text(text: str, *, font: Any, max_width: int, draw: Any) -> list[str]:
    """Greedy word-wrap. Returns list of lines that each fit within max_width."""
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


def render_caption(
    channel: str,
    period_label: str,
    stats: dict[str, Any],
    *,
    lang: str = "en",
) -> str:
    """Caption template the user can edit before publishing."""
    top_lines = []
    for idx, post in enumerate(stats["top_posts"], 1):
        text = _truncate(post.get("text") or "", 60)
        link = post.get("link") or ""
        top_lines.append(f"#{idx} {text} — {post['forwards']:,} fwd · {link}")
    top_block = "\n".join(top_lines)
    fwd_rate = f"{stats['mean_forward_rate'] * 100:.2f}%"

    if lang == "ru":
        return (
            f"📊 chappe-wrapped: {channel} · {period_label}\n\n"
            f"{stats['posts']:,} постов · {stats['comments']:,} комментариев\n"
            f"Mean forward-rate: {fwd_rate}\n"
            f"Лучший формат: {stats['best_format']}\n\n"
            f"Топ-3 по форвардам:\n{top_block}\n\n"
            f"Сгенерировано через chappe — open-source CLI для Telegram-аналитики.\n"
            f"github.com/crimeacs/chappe"
        )
    return (
        f"📊 chappe-wrapped: {channel} · {period_label}\n\n"
        f"{stats['posts']:,} posts · {stats['comments']:,} comments\n"
        f"Mean forward-rate: {fwd_rate}\n"
        f"Best format: {stats['best_format']}\n\n"
        f"Top 3 by forwards:\n{top_block}\n\n"
        f"Generated by chappe — open-source CLI for Telegram analytics.\n"
        f"github.com/crimeacs/chappe"
    )
