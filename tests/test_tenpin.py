from datetime import date
from fractions import Fraction

import pytest

from tenpin import (
    FrameError,
    _series_row,
    explain_game,
    handicap,
    main,
    parse_frame,
    parse_line,
    player_stats,
    read_league,
    render_card,
    score_frames,
    scored_so_far,
    simulate_lines,
    game_total,
)

PERFECT = "X X X X X X X X X XXX"
ALL_NINES = " ".join(["9-"] * 10)
WIKIPEDIA = "X 7/ 9- X -8 8/ -6 X X X81"


def _total(frames_text: str) -> int | None:
    return game_total(parse_line(f"Test: {frames_text}", 1))


def test_perfect_game_scores_300():
    assert _total(PERFECT) == 300


def test_open_frames_score_nine_each():
    assert _total(ALL_NINES) == 90


def test_wikipedia_example_scores_167():
    assert _total(WIKIPEDIA) == 167


def test_all_spares_with_fill_ball_scores_150():
    assert _total(" ".join(["5/"] * 9) + " 5/5") == 150


def test_running_totals_follow_each_frame():
    game = parse_line(f"Test: {WIKIPEDIA}", 1)
    running = [result.running for result in score_frames(game.frames)]
    assert running == [20, 39, 48, 66, 74, 84, 90, 120, 148, 167]


def test_pending_bonus_blocks_running_total():
    game = parse_line("Test: X 7", 1)
    results = score_frames(game.frames)
    assert results[0].running is None
    assert game_total(game) is None
    assert scored_so_far(game) == 0


def test_in_progress_game_reports_scored_so_far():
    game = parse_line("Test: 9- 9- 9-", 1)
    assert not game.complete
    assert scored_so_far(game) == 27


def test_spare_waits_for_its_next_roll():
    results = score_frames(parse_line("Test: 5/", 1).frames)
    assert results[0].value is None
    assert results[0].running is None


def test_rejects_digits_that_add_up_to_ten_without_a_slash():
    with pytest.raises(FrameError, match="'/'"):
        parse_frame("82", 1)


def test_rejects_slash_as_first_roll():
    with pytest.raises(FrameError):
        parse_frame("/", 1)


def test_rejects_extra_rolls_after_a_strike():
    with pytest.raises(FrameError, match="strike"):
        parse_frame("XX", 3)


def test_rejects_third_tenth_frame_roll_without_bonus():
    with pytest.raises(FrameError, match="third roll"):
        parse_frame("715", 10)


def test_tenth_frame_fill_ball_after_spare_is_accepted():
    frame = parse_frame("5/5", 10)
    assert frame.complete
    assert frame.pins == (5, 5, 5)


def test_slash_after_strike_then_miss_is_a_spare_on_fresh_rack():
    frame = parse_frame("X-/", 10)
    assert frame.pins == (10, 0, 10)


def test_rejects_slash_directly_after_strike_in_tenth_frame():
    with pytest.raises(FrameError):
        parse_frame("X/", 10)


def test_rejects_digit_that_clears_the_remaining_pins_without_slash():
    with pytest.raises(FrameError, match="'/'"):
        parse_frame("X73", 10)


def test_read_league_reports_the_line_number_of_each_error():
    text = "\n".join([
        "# header",
        "",
        "Ana: 9- 9- 9- 9- 9- 9- 9- 9- 9- 9-",
        "Cy: 9- 9- 9- 9- 9- 9- 9- 9- 9- 82",
    ])
    games, problems = read_league(text)
    assert [game.player for game in games] == ["Ana"]
    assert len(problems) == 1
    assert problems[0].line == 4
    assert problems[0].level == "error"


def test_read_league_warns_about_duplicates_and_in_progress_games():
    text = "\n".join([
        "Ana: 9- 9- 9- 9- 9- 9- 9- 9- 9- 9-",
        "Ana: 9- 9- 9- 9- 9- 9- 9- 9- 9- 9-",
        "Dee: X 7",
    ])
    _, problems = read_league(text)
    messages = {(problem.line, problem.message) for problem in problems}
    assert (2, "duplicate of line 1") in messages
    assert any(problem.line == 3 and "in progress" in problem.message for problem in problems)


def test_handicap_is_ninety_percent_of_gap_to_210_rounded_down():
    assert handicap(Fraction(200)) == 9
    assert handicap(Fraction(150)) == 54
    assert handicap(Fraction(375, 2)) == 20  # 187.5 -> 22.5 gap -> 20.25 -> 20


def test_handicap_is_zero_at_or_above_base():
    assert handicap(Fraction(215)) == 0
    assert handicap(Fraction(210)) == 0


def test_series_row_strike_and_spare_percentages():
    games = [
        parse_line(f"Ana: {PERFECT}", 1),
        parse_line(f"Ben: {ALL_NINES}", 2),
    ]
    stats = {row.player: row for row in player_stats(games)}
    ana = _series_row(stats["Ana"], 210, 90)
    ben = _series_row(stats["Ben"], 210, 90)
    assert ana["strike_pct"] == "100.0%"
    assert ana["spare_pct"] == "-"
    assert ben["strike_pct"] == "0.0%"
    assert ben["spare_pct"] == "0.0%"
    assert ben["open"] == 10
    assert ben["gutters"] == 10
    assert ben["average"] == "90.00"


def test_series_leaves_out_in_progress_games():
    games = [parse_line("Test: 9- 9-", 1), parse_line(f"Test: {ALL_NINES}", 2)]
    (stats,) = player_stats(games)
    assert stats.scores == [90]


def test_simulated_games_all_parse_as_complete_games():
    lines = simulate_lines("Sim", 25, seed=3)
    for number, line in enumerate(lines, start=1):
        game = parse_line(line, number)
        assert game.complete
        assert 0 <= game_total(game) <= 300


def test_simulation_is_repeatable_for_a_seed():
    assert simulate_lines("Sim", 5, seed=42) == simulate_lines("Sim", 5, seed=42)
    assert simulate_lines("Sim", 5, seed=42) != simulate_lines("Sim", 5, seed=43)


def test_scorecard_shows_frames_totals_and_final_score():
    card = render_card(parse_line(f"Ana: {PERFECT}", 1))
    assert "300" in card
    assert "Final score: 300" in card
    assert "XXX" in card


def test_explain_shows_strike_bonus_arithmetic():
    lines = explain_game(parse_line(f"Ana: {PERFECT}", 1))
    assert "strike: 10 + 10 + 10 = 30" in lines[0]
    assert lines[0].endswith("running 30")


def test_check_exit_codes_and_strict_mode(tmp_path, capsys):
    clean = tmp_path / "clean.txt"
    clean.write_text(f"Ana: {ALL_NINES}\n", encoding="utf-8")
    broken = tmp_path / "broken.txt"
    broken.write_text("Ana: 82\n", encoding="utf-8")
    partial = tmp_path / "partial.txt"
    partial.write_text("Dee: X 7\n", encoding="utf-8")

    assert main(["check", str(clean)]) == 0
    assert main(["check", str(broken)]) == 1
    assert "broken.txt:1: error:" in capsys.readouterr().out
    assert main(["check", str(partial)]) == 0
    assert main(["check", str(partial), "--strict"]) == 1


def test_gate_fails_below_minimum_and_passes_above(tmp_path, capsys):
    league = tmp_path / "league.txt"
    league.write_text(f"Ben: {ALL_NINES}\n", encoding="utf-8")

    assert main(["gate", str(league), "--min-average", "150"]) == 1
    assert "Ben: average 90.00 vs minimum 150.00: FAIL" in capsys.readouterr().out
    assert main(["gate", str(league), "--min-average", "80"]) == 0


def test_score_date_filters_skip_undated_and_out_of_range_games(tmp_path, capsys):
    league = tmp_path / "league.txt"
    league.write_text("\n".join([
        f"2026-10-01 Ana: {ALL_NINES}",
        f"Ana: {ALL_NINES}",
        f"2026-10-08 Ana: {PERFECT}",
    ]) + "\n", encoding="utf-8")

    assert main(["score", str(league), "--since", "2026-10-05"]) == 0
    out = capsys.readouterr().out
    assert "2026-10-08" in out
    assert "2026-10-01" not in out
    assert "300" in out


def test_export_writes_csv_header_and_totals(tmp_path, capsys):
    league = tmp_path / "league.txt"
    league.write_text(f"Ana: {PERFECT}\n", encoding="utf-8")

    assert main(["export", str(league)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "line,date,player,frames,complete,total,so_far,tokens"
    assert lines[1].split(",")[5] == "300"


def test_card_on_an_invalid_line_is_a_usage_error(tmp_path, capsys):
    league = tmp_path / "league.txt"
    league.write_text("Ana: 9- 9- 9- 9- 9- 9- 9- 9- 9- 82\n", encoding="utf-8")

    assert main(["card", str(league), "--line", "1"]) == 2
    assert "not a valid game" in capsys.readouterr().err


def test_missing_file_returns_usage_exit_code(tmp_path):
    assert main(["check", str(tmp_path / "nope.txt")]) == 2


def test_date_helper_is_used_for_dated_lines():
    game = parse_line(f"2026-10-08 Ana: {PERFECT}", 1)
    assert game.game_date == date(2026, 10, 8)
    assert game.player == "Ana"
