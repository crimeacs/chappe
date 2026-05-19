from datetime import datetime, timezone

from chappe.analytics import (
    engagement_score,
    filter_posts_by_period,
    find_outliers,
    generate_ideas,
    post_timing_analysis,
    rank_posts,
    share_velocity_analysis,
)


def test_engagement_score_weights_forwards_highest():
    assert engagement_score(views=1000, forwards=10, replies=5, reactions=7) == 67


def test_rank_posts_by_forwards():
    posts = [
        {"id": "1", "views": 100, "forwards": 1, "replies": 10, "reactions": 10},
        {"id": "2", "views": 100, "forwards": 7, "replies": 0, "reactions": 0},
    ]
    assert rank_posts(posts, by="forwards", limit=1)[0]["id"] == "2"


def test_outliers_include_ratio():
    posts = [
        {"id": "1", "views": 100, "forwards": 1, "replies": 0, "reactions": 0},
        {"id": "2", "views": 100, "forwards": 100, "replies": 0, "reactions": 0},
    ]
    assert find_outliers(posts, limit=1)[0]["id"] == "2"


def test_generate_ideas_uses_comment_questions():
    ideas = generate_ideas(
        [{"id": "1", "text": "Claude agents and Telegram growth", "forwards": 10}],
        [{"id": "c1", "post_id": "1", "text": "Как это настроить?"}],
        count=5,
    )
    assert any(idea["title"] == "Ответить на вопрос аудитории" for idea in ideas)


def test_post_timing_analysis_groups_by_hour_and_weekday():
    posts = [
        {
            "id": "1",
            "date": "2026-05-18T10:00:00+00:00",
            "views": 100,
            "forwards": 10,
            "replies": 1,
            "reactions": 0,
        },
        {
            "id": "2",
            "date": "2026-05-19T10:30:00+00:00",
            "views": 200,
            "forwards": 20,
            "replies": 2,
            "reactions": 0,
        },
    ]

    timing = post_timing_analysis(posts, timezone_name="UTC")

    assert timing["posts_analyzed"] == 2
    assert timing["best_hours"][0]["label"] == "10:00"
    assert timing["best_hours"][0]["post_count"] == 2
    assert timing["cadence"]["median_gap_hours"] == 24.5


def test_filter_posts_by_period():
    posts = [
        {"id": "new", "date": "2026-05-18T10:00:00+00:00"},
        {"id": "old", "date": "2026-01-01T10:00:00+00:00"},
    ]

    filtered = filter_posts_by_period(
        posts,
        "30d",
        now=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )

    assert [post["id"] for post in filtered] == ["new"]


def test_share_velocity_analysis_uses_snapshot_deltas():
    posts = [
        {
            "id": "1",
            "date": "2026-05-19T10:00:00+00:00",
            "text": "launch",
            "link": "https://t.me/x/1",
        }
    ]
    snapshots = [
        {
            "post_id": "1",
            "captured_at": "2026-05-19T11:00:00+00:00",
            "views": 100,
            "forwards": 10,
            "replies": 1,
            "reactions": 0,
        },
        {
            "post_id": "1",
            "captured_at": "2026-05-19T12:00:00+00:00",
            "views": 160,
            "forwards": 25,
            "replies": 3,
            "reactions": 0,
        },
    ]

    velocity = share_velocity_analysis(posts, snapshots)

    assert velocity["delta_intervals"] == 1
    assert velocity["top_forward_gainers"][0]["forwards_delta"] == 15
    assert velocity["top_activity_gainers"][0]["views_delta"] == 60
    assert velocity["top_forward_gainers"][0]["age_bucket"] == "1-3h"


def test_share_velocity_warns_when_only_views_change():
    posts = [{"id": "1", "date": "2026-05-19T10:00:00+00:00"}]
    snapshots = [
        {"post_id": "1", "captured_at": "2026-05-19T11:00:00+00:00", "views": 100, "forwards": 10},
        {"post_id": "1", "captured_at": "2026-05-19T12:00:00+00:00", "views": 120, "forwards": 10},
    ]

    velocity = share_velocity_analysis(posts, snapshots)

    assert velocity["top_forward_gainers"] == []
    assert velocity["top_activity_gainers"][0]["views_delta"] == 20
    assert "forward_count did not increase" in velocity["warnings"][0]
