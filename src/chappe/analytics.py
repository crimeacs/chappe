from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo


STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "that",
    "this",
    "from",
    "https",
    "который",
    "которые",
    "чтобы",
    "теперь",
    "просто",
    "можно",
    "через",
    "только",
    "когда",
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_posts_by_period(
    posts: list[dict[str, Any]],
    period: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not period or period == "all":
        return posts
    match = re.fullmatch(r"(\d+)([dwmy])", period.strip().lower())
    if not match:
        return posts
    amount = int(match.group(1))
    unit = match.group(2)
    days = amount
    if unit == "w":
        days = amount * 7
    elif unit == "m":
        days = amount * 30
    elif unit == "y":
        days = amount * 365
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return [post for post in posts if (parse_datetime(post.get("date")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]


def engagement_score(views: int = 0, forwards: int = 0, replies: int = 0, reactions: int = 0) -> int:
    return int(forwards or 0) * 5 + int(replies or 0) * 2 + int(reactions or 0)


def enrich_post(row: dict[str, Any]) -> dict[str, Any]:
    views = int(row.get("views") or 0)
    forwards = int(row.get("forwards") or 0)
    replies = int(row.get("replies") or 0)
    reactions = int(row.get("reactions") or 0)
    score = engagement_score(views, forwards, replies, reactions)
    return {
        **row,
        "engagement_score": score,
        "forward_rate": forwards / views if views else 0,
        "reaction_rate": reactions / views if views else 0,
        "comment_rate": replies / views if views else 0,
    }


def rank_posts(posts: list[dict[str, Any]], *, by: str, limit: int) -> list[dict[str, Any]]:
    enriched = [enrich_post(post) for post in posts]
    key = "engagement_score" if by == "engagement" else by
    return sorted(enriched, key=lambda row: row.get(key) or 0, reverse=True)[:limit]


def _median(values: list[int | float]) -> float:
    return round(float(median(values)), 6) if values else 0.0


def _mean(values: list[int | float]) -> float:
    return round(float(mean(values)), 6) if values else 0.0


def _timing_bucket(label: str, posts: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = [enrich_post(post) for post in posts]
    top = sorted(enriched, key=lambda row: row.get("engagement_score") or 0, reverse=True)[:3]
    return {
        "label": label,
        "post_count": len(posts),
        "median_views": _median([int(post.get("views") or 0) for post in enriched]),
        "median_forwards": _median([int(post.get("forwards") or 0) for post in enriched]),
        "median_forward_rate": _median([float(post.get("forward_rate") or 0) for post in enriched]),
        "median_comment_rate": _median([float(post.get("comment_rate") or 0) for post in enriched]),
        "median_engagement_score": _median([int(post.get("engagement_score") or 0) for post in enriched]),
        "sample_post_ids": [post.get("id") for post in top],
    }


def post_timing_analysis(
    posts: list[dict[str, Any]],
    *,
    timezone_name: str = "UTC",
    limit: int = 10,
) -> dict[str, Any]:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
        timezone_name = "UTC"

    dated: list[tuple[dict[str, Any], datetime]] = []
    for post in posts:
        parsed = parse_datetime(post.get("date"))
        if parsed:
            dated.append((post, parsed.astimezone(tz)))

    by_hour: dict[int, list[dict[str, Any]]] = {}
    by_weekday: dict[int, list[dict[str, Any]]] = {}
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for post, local_dt in dated:
        by_hour.setdefault(local_dt.hour, []).append(post)
        by_weekday.setdefault(local_dt.weekday(), []).append(post)
        slot = f"{WEEKDAYS[local_dt.weekday()]} {local_dt.hour:02d}:00"
        by_slot.setdefault(slot, []).append(post)

    hour_rows = [_timing_bucket(f"{hour:02d}:00", group) for hour, group in by_hour.items()]
    weekday_rows = [_timing_bucket(WEEKDAYS[day], group) for day, group in by_weekday.items()]
    slot_rows = [_timing_bucket(slot, group) for slot, group in by_slot.items()]

    def sort_key(row: dict[str, Any]) -> tuple[float, float, float, int]:
        return (
            row["median_forward_rate"],
            row["median_forwards"],
            row["median_engagement_score"],
            row["post_count"],
        )

    dates = sorted(local_dt for _, local_dt in dated)
    gaps = [
        (current - previous).total_seconds() / 3600
        for previous, current in zip(dates, dates[1:])
        if current > previous
    ]
    span_days = max((dates[-1] - dates[0]).total_seconds() / 86400, 1) if len(dates) > 1 else 0
    caveats: list[str] = []
    if len(dated) < 30:
        caveats.append("Timing sample has fewer than 30 dated posts; treat slot rankings as directional.")

    return {
        "timezone": timezone_name,
        "posts_analyzed": len(dated),
        "cadence": {
            "first_post_at": dates[0].isoformat() if dates else None,
            "last_post_at": dates[-1].isoformat() if dates else None,
            "span_days": round(span_days, 3) if dates else 0,
            "posts_per_week": round(len(dated) / span_days * 7, 3) if span_days else len(dated),
            "median_gap_hours": _median(gaps),
        },
        "best_hours": sorted(hour_rows, key=sort_key, reverse=True)[:limit],
        "best_weekdays": sorted(weekday_rows, key=sort_key, reverse=True),
        "best_weekday_hours": sorted(slot_rows, key=sort_key, reverse=True)[:limit],
        "caveats": caveats,
    }


def _age_bucket(hours: float) -> str:
    if hours < 1:
        return "0-1h"
    if hours < 3:
        return "1-3h"
    if hours < 6:
        return "3-6h"
    if hours < 12:
        return "6-12h"
    if hours < 24:
        return "12-24h"
    if hours < 72:
        return "1-3d"
    if hours < 168:
        return "3-7d"
    return "7d+"


def share_velocity_analysis(
    posts: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    post_map = {str(post.get("id")): post for post in posts}
    post_dates = {post_id: parse_datetime(post.get("date")) for post_id, post in post_map.items()}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        post_id = str(snapshot.get("post_id"))
        if post_id in post_map:
            grouped.setdefault(post_id, []).append(snapshot)

    intervals: list[dict[str, Any]] = []
    for post_id, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.get("captured_at") or "")
        for previous, current in zip(ordered, ordered[1:]):
            previous_at = parse_datetime(previous.get("captured_at"))
            current_at = parse_datetime(current.get("captured_at"))
            post_date = post_dates.get(post_id)
            if not previous_at or not current_at or not post_date or current_at <= previous_at:
                continue
            interval_hours = (current_at - previous_at).total_seconds() / 3600
            deltas = {
                "views_delta": max(0, int(current.get("views") or 0) - int(previous.get("views") or 0)),
                "forwards_delta": max(0, int(current.get("forwards") or 0) - int(previous.get("forwards") or 0)),
                "replies_delta": max(0, int(current.get("replies") or 0) - int(previous.get("replies") or 0)),
                "reactions_delta": max(0, int(current.get("reactions") or 0) - int(previous.get("reactions") or 0)),
            }
            if not any(deltas.values()):
                continue
            age_hours = (current_at - post_date).total_seconds() / 3600
            post = post_map[post_id]
            intervals.append(
                {
                    "post_id": post_id,
                    "link": post.get("link"),
                    "text": (post.get("text") or "")[:240],
                    "captured_at": current_at.isoformat(),
                    "interval_hours": round(interval_hours, 6),
                    "post_age_hours": round(age_hours, 6),
                    "age_bucket": _age_bucket(age_hours),
                    **deltas,
                    "forwards_per_hour": round(deltas["forwards_delta"] / interval_hours, 6),
                    "views_per_hour": round(deltas["views_delta"] / interval_hours, 6),
                }
            )

    bucket_rows: list[dict[str, Any]] = []
    for bucket in ["0-1h", "1-3h", "3-6h", "6-12h", "12-24h", "1-3d", "3-7d", "7d+"]:
        rows = [row for row in intervals if row["age_bucket"] == bucket]
        if not rows:
            continue
        bucket_rows.append(
            {
                "age_bucket": bucket,
                "interval_count": len(rows),
                "forwards_delta": sum(row["forwards_delta"] for row in rows),
                "views_delta": sum(row["views_delta"] for row in rows),
                "median_forwards_per_hour": _median([row["forwards_per_hour"] for row in rows]),
                "mean_forwards_per_hour": _mean([row["forwards_per_hour"] for row in rows]),
            }
        )

    warnings: list[str] = []
    if not intervals:
        warnings.append("No metric deltas observed. Run sync repeatedly over time to measure share velocity.")
    if intervals and not any(row["forwards_delta"] > 0 for row in intervals):
        warnings.append("Snapshot deltas exist, but forward_count did not increase in the observed intervals.")

    top_forward_gainers = sorted(
        [row for row in intervals if row["forwards_delta"] > 0],
        key=lambda row: (row["forwards_delta"], row["forwards_per_hour"], row["views_delta"]),
        reverse=True,
    )[:limit]
    top_activity_gainers = sorted(
        intervals,
        key=lambda row: (
            row["forwards_delta"],
            row["views_delta"],
            row["replies_delta"],
            row["views_per_hour"],
        ),
        reverse=True,
    )[:limit]

    return {
        "method": "Forward/share velocity is inferred from Telegram forward_count deltas between local sync snapshots.",
        "posts_considered": len(post_map),
        "snapshots_considered": sum(len(rows) for rows in grouped.values()),
        "delta_intervals": len(intervals),
        "age_buckets": bucket_rows,
        "top_forward_gainers": top_forward_gainers,
        "top_activity_gainers": top_activity_gainers,
        "warnings": warnings,
    }


def find_outliers(posts: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    enriched = [enrich_post(post) for post in posts]
    scores = [post["engagement_score"] for post in enriched]
    if not scores:
        return []
    baseline = median(scores) or 1
    for post in enriched:
        post["outlier_ratio"] = round(post["engagement_score"] / baseline, 3)
    return sorted(enriched, key=lambda row: row["outlier_ratio"], reverse=True)[:limit]


def mine_terms(texts: list[str], *, limit: int = 30) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for text in texts:
        for word in re.findall(r"[\wА-Яа-яЁё-]{4,}", text.lower()):
            if word not in STOPWORDS and not word.startswith("http"):
                counter[word] += 1
    return [{"term": term, "count": count} for term, count in counter.most_common(limit)]


def generate_ideas(posts: list[dict[str, Any]], comments: list[dict[str, Any]], *, count: int) -> list[dict]:
    top_terms = mine_terms([post.get("text") or "" for post in rank_posts(posts, by="engagement", limit=50)])
    comment_questions = [
        c
        for c in comments
        if "?" in (c.get("text") or "") or any(w in (c.get("text") or "").lower() for w in ["как", "why", "what"])
    ]
    ideas: list[dict[str, Any]] = []
    for term in top_terms[: max(3, count)]:
        ideas.append(
            {
                "title": f"Разобрать тему: {term['term']}",
                "rationale": "Term appears repeatedly in historically strong posts.",
                "evidence": {"term_count": term["count"]},
            }
        )
    for comment in comment_questions[:count]:
        ideas.append(
            {
                "title": "Ответить на вопрос аудитории",
                "rationale": "Audience question found in comments.",
                "evidence": {
                    "post_id": comment.get("post_id"),
                    "comment_id": comment.get("id"),
                    "text": (comment.get("text") or "")[:240],
                },
            }
        )
    return ideas[:count]
