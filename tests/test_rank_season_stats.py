"""
Season stats (wins / games / win rate / streak from HSJeeves uploads) in /rank.
The DB query (fetch_season_stats) is exercised against a real Postgres separately;
here: the formatting helpers and the rendered /rank sections with fakes.
Run with: pytest tests/
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from bot.commands import rank_commands
from bot.commands.rank_commands import RankCommands
from bot.services import rank_tracker_data
from bot.services.rank_tracker_data import format_season_stats, season_start_utc


# ── helpers ────────────────────────────────────────────────────────────────

def test_format_season_stats():
    assert format_season_stats({"wins": 107, "games": 199, "streak": 2}) == ("107/199", "53.8%", "2")
    assert format_season_stats({"wins": 0, "games": 5, "streak": 0}) == ("0/5", "0.0%", "0")
    assert format_season_stats({"wins": 5, "games": 5, "streak": 5}) == ("5/5", "100.0%", "5")


def test_format_season_stats_hides_missing_values():
    assert format_season_stats(None) is None
    assert format_season_stats({}) is None
    assert format_season_stats({"wins": None, "games": None, "streak": None}) is None
    assert format_season_stats({"wins": 3, "games": None, "streak": 1}) is None
    # streak alone missing: the record is still shown
    assert format_season_stats({"wins": 3, "games": 4, "streak": None}) == ("3/4", "75.0%", "-")
    # zero games must not divide by zero
    assert format_season_stats({"wins": 0, "games": 0, "streak": 0}) == ("0/0", "-", "0")


def test_season_start_is_first_of_month_utc():
    now = datetime(2026, 11, 27, 13, 45, 12, 345, tzinfo=timezone.utc)
    assert season_start_utc(now) == datetime(2026, 11, 1, tzinfo=timezone.utc)


# ── rendered sections (fakes instead of a database) ────────────────────────

class _Entry:
    def __init__(self, rank):
        self.rank, self.rating = rank, None


def _stats():
    return {"season_score": 12.5, "today_dps": 1.0, "best_rank": 5, "best_legend_count": 100, "latest_legend_count": 100}


@pytest.fixture()
def cog(monkeypatch):
    @asynccontextmanager
    async def fake_db():
        yield object()

    async def fake_entry(conn, battletag, region, mode):
        return _Entry(123), 157

    async def fake_mode_stats(*args, **kwargs):
        return _stats()

    monkeypatch.setattr(rank_commands, "get_db", fake_db)
    monkeypatch.setattr(RankCommands, "_fetch_entry", staticmethod(fake_entry))
    monkeypatch.setattr(rank_commands, "_fetch_mode_stats", fake_mode_stats)
    return RankCommands.__new__(RankCommands)  # no bot needed: the section builders don't use it


def _season(monkeypatch, result):
    async def fake(conn, battletag, region, since):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(rank_tracker_data, "fetch_season_stats", fake)


@pytest.mark.asyncio
async def test_default_section_shows_both_formats(cog, monkeypatch):
    _season(monkeypatch, {
        "standard": {"wins": 107, "games": 199, "streak": 2},
        "wild": {"wins": 116, "games": 214, "streak": 0},
    })
    text = await cog._section_default("Deesnow#2794", "EU")
    lines = text.splitlines()
    row = {l.split("  ")[1].strip(): l for l in lines if l.startswith("  ") and l.strip()}
    assert "107/199" in row["Season W/G"] and "116/214" in row["Season W/G"]
    assert "53.8%" in row["Win Rate"] and "54.2%" in row["Win Rate"]
    assert row["Win Streak"].split()[-2:] == ["2", "0"]
    assert text.count("```") == 2 and len(text) < 600  # well within Discord's 2000 characters


@pytest.mark.asyncio
async def test_default_section_one_format_without_data_shows_dash(cog, monkeypatch):
    _season(monkeypatch, {"standard": {"wins": 10, "games": 20, "streak": 1}})
    text = await cog._section_default("Deesnow#2794", "EU")
    wins_row = next(l for l in text.splitlines() if "Season W/G" in l)
    assert wins_row.split()[-2:] == ["10/20", "-"]


@pytest.mark.asyncio
async def test_default_section_hides_rows_without_data(cog, monkeypatch):
    for empty in ({}, {"standard": {"wins": None, "games": None, "streak": None}}):
        _season(monkeypatch, empty)
        text = await cog._section_default("Deesnow#2794", "EU")
        assert "Season W/G" not in text and "Win Rate" not in text and "Win Streak" not in text
        assert "Season Score" in text  # the rest of the table is unchanged


@pytest.mark.asyncio
async def test_lookup_failure_does_not_break_rank(cog, monkeypatch):
    _season(monkeypatch, RuntimeError("db hiccup"))
    text = await cog._section_default("Deesnow#2794", "EU")
    assert "Season Score" in text and "Season W/G" not in text


@pytest.mark.asyncio
async def test_default_section_wide_numbers_keep_columns_aligned(cog, monkeypatch):
    _season(monkeypatch, {
        "standard": {"wins": 12345, "games": 23456, "streak": 12},
        "wild": {"wins": 1, "games": 2, "streak": 1},
    })
    text = await cog._section_default("Deesnow#2794", "EU")
    table = [l for l in text.splitlines() if l.startswith("  ")]
    # the Wild column starts at the same offset on every row
    rows = [l.rstrip() for l in table if l.strip() and "---" not in l]
    offsets = {len(l) - len(l.split()[-1]) for l in rows}
    assert len(rows) >= 7 and len(offsets) == 1


@pytest.mark.asyncio
async def test_single_mode_line(cog, monkeypatch):
    _season(monkeypatch, {"standard": {"wins": 107, "games": 199, "streak": 2}})
    text = await cog._section_single("Deesnow#2794", "EU", "standard", "Standard")
    assert "Season Record: 107/199 (53.8%)" in text and "Win Streak: 2" in text
    _season(monkeypatch, {})
    assert "Season Record" not in await cog._section_single("Deesnow#2794", "EU", "standard", "Standard")
    assert "Season Record" not in await cog._section_single("Deesnow#2794", "EU", "classic", "Classic")
