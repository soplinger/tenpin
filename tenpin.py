"""Tenpin: offline ten-pin bowling scorer for plain-text league files.

Each line of a league file is one game:

    [YYYY-MM-DD] Player name: frame frame ... frame

Frames 1-9 are one token each ("X", "7/", "9-", "45"). The tenth frame holds
up to three rolls ("9-", "X7/", "XX8"). "X" is a strike, "/" a spare and "-"
a miss. Blank lines and lines starting with "#" are ignored.

Standard library only. No network, no accounts, no state beyond the file.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from math import floor
from pathlib import Path

PINS = 10
FRAMES = 10
DEFAULT_BASE = 210
DEFAULT_PERCENT = 90


class FrameError(ValueError):
    """A frame token breaks the notation or bowling rules."""


class LineError(ValueError):
    """A league line cannot be read as a game."""


class UsageError(Exception):
    """A command was asked for something the file cannot provide."""


@dataclass(frozen=True)
class Frame:
    symbols: str  # the frame as written, e.g. "7/" or "X81"
    pins: tuple[int, ...]  # pins knocked down by each roll
    complete: bool

    @property
    def strike(self) -> bool:
        return bool(self.pins) and self.pins[0] == PINS

    @property
    def spare(self) -> bool:
        return not self.strike and len(self.pins) >= 2 and sum(self.pins[:2]) == PINS

    @property
    def is_open(self) -> bool:
        return self.complete and not self.strike and not self.spare


@dataclass(frozen=True)
class Game:
    line: int
    player: str
    game_date: date | None
    frames: tuple[Frame, ...]
    tokens: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return len(self.frames) == FRAMES and self.frames[-1].complete


@dataclass(frozen=True)
class Problem:
    line: int
    level: str  # "error" or "warning"
    message: str


@dataclass(frozen=True)
class FrameResult:
    number: int
    frame: Frame
    value: int | None  # points for this frame, bonus included; None while pending
    running: int | None  # cumulative score; None once any frame is pending
    bonus: tuple[int, ...]  # pins from later rolls counted as this frame's bonus


@dataclass
class PlayerStats:
    player: str
    scores: list[int] = field(default_factory=list)
    first_balls: int = 0
    strikes: int = 0
    spare_chances: int = 0
    spares: int = 0
    opens: int = 0
    gutters: int = 0


# ---------------------------------------------------------------- parsing


def _earns_bonus(pins: list[int]) -> bool:
    """True when the rolls so far (tenth frame only) earn a third roll."""
    return bool(pins) and (pins[0] == PINS or (len(pins) >= 2 and pins[0] + pins[1] == PINS))


def _roll_pins(symbol: str, standing: int, fresh: bool, number: int) -> int:
    """Pins knocked down by one symbol, or FrameError if the notation is wrong.

    `fresh` means no ball has been thrown at this rack yet, so only a strike
    is possible; `standing` is the number of pins still up.
    """
    where = f"frame {number}"
    if symbol == "X":
        if not fresh:
            raise FrameError(f"{where}: 'X' is only valid as the first roll on a fresh rack")
        return PINS
    if symbol == "/":
        if fresh:
            raise FrameError(f"{where}: '/' needs an earlier roll on the same rack")
        return standing
    if symbol == "-":
        return 0
    if symbol in "0123456789":
        pins = int(symbol)
        if pins > standing:
            raise FrameError(f"{where}: {pins} pins but only {standing} standing")
        if pins == standing:
            raise FrameError(f"{where}: {pins} clears the rack, write '/' for a spare")
        return pins
    raise FrameError(f"{where}: unknown symbol {symbol!r}")


def parse_frame(token: str, number: int) -> Frame:
    """Parse one frame token. Frame numbers run from 1 to 10."""
    last = number == FRAMES
    token = token.upper()
    pins: list[int] = []
    standing, fresh = PINS, True
    for index, symbol in enumerate(token):
        where = f"frame {number}"
        if not last and pins and pins[0] == PINS:
            raise FrameError(f"{where}: a strike ends the frame, {token!r} has extra rolls")
        if not last and index >= 2:
            raise FrameError(f"{where}: {token!r} has too many rolls")
        if last and index >= 3:
            raise FrameError(f"{where}: {token!r} has too many rolls")
        if last and index == 2 and not _earns_bonus(pins):
            raise FrameError(f"{where}: a third roll needs a strike or spare first")
        pin = _roll_pins(symbol, standing, fresh, number)
        pins.append(pin)
        if pin == standing:
            standing, fresh = PINS, True
        else:
            standing, fresh = standing - pin, False
    if last:
        complete = len(pins) == 3 or (len(pins) == 2 and not _earns_bonus(pins))
    else:
        complete = len(pins) == 2 or pins[0] == PINS
    return Frame(token, tuple(pins), complete)


_DATE_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2})\s+")


def parse_line(text: str, line: int) -> Game:
    """Parse one non-blank, non-comment league line into a Game."""
    rest = text
    game_date = None
    match = _DATE_PREFIX.match(text)
    if match:
        try:
            game_date = date.fromisoformat(match.group(1))
        except ValueError:
            raise LineError(f"invalid date {match.group(1)!r}") from None
        rest = text[match.end():]
    head, colon, body = rest.partition(":")
    if not colon:
        raise LineError("expected 'Player: frames'")
    player = " ".join(head.split())
    if not player:
        raise LineError("missing player name before ':'")
    tokens = tuple(token.upper() for token in body.split())
    if not tokens:
        raise LineError(f"{player}: no frames after ':'")
    if len(tokens) > FRAMES:
        raise LineError(f"{len(tokens)} frames given, a game has {FRAMES}")
    frames = []
    for number, token in enumerate(tokens, start=1):
        try:
            frame = parse_frame(token, number)
        except FrameError as exc:
            raise LineError(str(exc)) from None
        if not frame.complete and number != len(tokens):
            raise LineError(f"frame {number}: {token!r} is unfinished but more frames follow")
        frames.append(frame)
    return Game(line, player, game_date, tuple(frames), tokens)


def read_league(text: str) -> tuple[list[Game], list[Problem]]:
    """Read every game in a league file, collecting line-numbered problems."""
    games: list[Game] = []
    problems: list[Problem] = []
    first_seen: dict[tuple, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        content = raw.strip()
        if not content or content.startswith("#"):
            continue
        try:
            game = parse_line(content, number)
        except LineError as exc:
            problems.append(Problem(number, "error", str(exc)))
            continue
        games.append(game)
        key = (game.game_date, game.player.casefold(), game.tokens)
        if key in first_seen:
            problems.append(Problem(number, "warning", f"duplicate of line {first_seen[key]}"))
        else:
            first_seen[key] = number
        if not game.complete:
            problems.append(Problem(number, "warning", _progress_text(game)))
    return games, problems


def load_league(path: str) -> tuple[list[Game], list[Problem]]:
    return read_league(Path(path).read_text(encoding="utf-8"))


def _progress_text(game: Game) -> str:
    if len(game.frames) < FRAMES:
        return f"game in progress: {len(game.frames)} of {FRAMES} frames bowled"
    return "game in progress: tenth frame still needs a roll"


# ---------------------------------------------------------------- scoring


def score_frames(frames: tuple[Frame, ...] | list[Frame]) -> list[FrameResult]:
    """Score each frame, adding strike and spare bonuses from later rolls."""
    rolls = [pin for frame in frames for pin in frame.pins]
    results: list[FrameResult] = []
    total: int | None = 0
    start = 0
    for number, frame in enumerate(frames, start=1):
        end = start + len(frame.pins)
        value: int | None = None
        bonus: tuple[int, ...] = ()
        if frame.complete:
            if number == FRAMES:
                value = sum(frame.pins)
            elif frame.strike:
                bonus = tuple(rolls[end:end + 2])
                if len(bonus) == 2:
                    value = PINS + sum(bonus)
            elif frame.spare:
                bonus = tuple(rolls[end:end + 1])
                if len(bonus) == 1:
                    value = PINS + bonus[0]
            else:
                value = sum(frame.pins)
        if value is not None and total is not None:
            total += value
        else:
            total = None
        results.append(FrameResult(number, frame, value, total, bonus))
        start = end
    return results


def game_total(game: Game) -> int | None:
    """Final score, or None while the game is still in progress."""
    if not game.complete:
        return None
    return score_frames(game.frames)[-1].running


def scored_so_far(game: Game) -> int:
    known = [result.running for result in score_frames(game.frames) if result.running is not None]
    return known[-1] if known else 0


def handicap(average: Fraction, base: int = DEFAULT_BASE, percent: int = DEFAULT_PERCENT) -> int:
    """Handicap as a percentage of the gap to the base, rounded down."""
    gap = base - average
    if gap <= 0:
        return 0
    return floor(gap * percent / 100)


def player_stats(games: list[Game]) -> list[PlayerStats]:
    """Per-player figures from complete games only, sorted by name."""
    table: dict[str, PlayerStats] = {}
    for game in games:
        if not game.complete:
            continue
        stats = table.setdefault(game.player.casefold(), PlayerStats(game.player))
        stats.scores.append(game_total(game))
        for frame in game.frames:
            stats.first_balls += 1
            if frame.strike:
                stats.strikes += 1
            else:
                stats.spare_chances += 1
                if frame.spare:
                    stats.spares += 1
                else:
                    stats.opens += 1
            stats.gutters += sum(1 for pin in frame.pins if pin == 0)
    return sorted(table.values(), key=lambda stats: stats.player.casefold())


def select_games(
    games: list[Game],
    player: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[Game]:
    selected = []
    for game in games:
        if player and game.player.casefold() != player.casefold():
            continue
        if since or until:
            if game.game_date is None:
                continue
            if since and game.game_date < since:
                continue
            if until and game.game_date > until:
                continue
        selected.append(game)
    return selected


# ---------------------------------------------------------------- formatting


def _fmt(value: Fraction, places: int = 2) -> str:
    quantum = Decimal(1).scaleb(-places)
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return str(exact.quantize(quantum, rounding=ROUND_HALF_UP))


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "-"
    return f"{_fmt(Fraction(part * 100, whole), 1)}%"


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """Left-aligned text table. `columns` is (key, heading) pairs."""
    grid = [[heading for _, heading in columns]]
    grid += [[str(row[key]) for key, _ in columns] for row in rows]
    widths = [max(len(line[i]) for line in grid) for i in range(len(columns))]
    rendered = []
    for index, line in enumerate(grid):
        rendered.append("  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip())
        if index == 0:
            rendered.append("  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def _emit_json(rows: list[dict]) -> None:
    print(json.dumps(rows, indent=2))


def render_card(game: Game) -> str:
    """ASCII scorecard with frame numbers, rolls and cumulative totals."""
    results = score_frames(game.frames)
    widths = [8] * (FRAMES - 1) + [12]
    border = "+" + "+".join("-" * width for width in widths) + "+"
    numbers, symbols, totals = [], [], []
    for number in range(1, FRAMES + 1):
        if number <= len(results):
            result = results[number - 1]
            symbols.append(result.frame.symbols)
            totals.append("" if result.running is None else str(result.running))
        else:
            symbols.append("")
            totals.append("")
        numbers.append(str(number))

    def cells(values: list[str]) -> str:
        return "|" + "|".join(f"{value:^{width}}" for value, width in zip(values, widths)) + "|"

    when = game.game_date.isoformat() if game.game_date else "undated"
    footer = f"Final score: {game_total(game)}" if game.complete else _progress_text(game)
    return "\n".join([
        f"{game.player}  {when}  (line {game.line})",
        border,
        cells(numbers),
        cells(symbols),
        border,
        cells(totals),
        border,
        footer,
    ])


def _explain_frame(result: FrameResult) -> str:
    frame = result.frame
    label = f"frame {result.number:>2}  {frame.symbols:<4} "
    if result.number < FRAMES and (frame.strike or frame.spare):
        kind = "strike" if frame.strike else "spare"
        needed = (2 if frame.strike else 1) - len(result.bonus)
        if result.value is None:
            text = f"{kind}, waiting for {needed} more roll(s)"
        else:
            parts = [str(PINS)] + [str(pin) for pin in result.bonus]
            text = f"{kind}: {' + '.join(parts)} = {result.value}"
    elif not frame.complete:
        text = "frame not finished"
    else:
        kind = "tenth" if result.number == FRAMES else "open"
        text = f"{kind}: {' + '.join(str(pin) for pin in frame.pins)} = {result.value}"
    running = "running -" if result.running is None else f"running {result.running}"
    return f"{label}{text}  {running}"


def explain_game(game: Game) -> list[str]:
    return [_explain_frame(result) for result in score_frames(game.frames)]


# ---------------------------------------------------------------- simulation


def _random_frame(rng: random.Random, number: int, strike_rate: float, spare_rate: float) -> str:
    """One legal frame token, built with the same rack rules the parser uses."""
    symbols: list[str] = []
    pins: list[int] = []
    standing, fresh = PINS, True
    while True:
        if fresh:
            pin = PINS if rng.random() < strike_rate else rng.randint(0, PINS - 1)
        elif rng.random() < spare_rate:
            pin = standing
        else:
            pin = rng.randint(0, standing - 1)
        if fresh and pin == PINS:
            symbols.append("X")
        elif not fresh and pin == standing:
            symbols.append("/")
        elif pin == 0:
            symbols.append("-")
        else:
            symbols.append(str(pin))
        pins.append(pin)
        if pin == standing:
            standing, fresh = PINS, True
        else:
            standing, fresh = standing - pin, False
        if number < FRAMES:
            if pins[0] == PINS or len(pins) == 2:
                break
        elif len(pins) == 3 or (len(pins) == 2 and not _earns_bonus(pins)):
            break
    return "".join(symbols)


def simulate_lines(
    player: str,
    games: int,
    seed: int,
    strike_rate: float = 0.25,
    spare_rate: float = 0.5,
    game_date: date | None = None,
) -> list[str]:
    """Seeded random games in league-file format. The same seed gives the same games."""
    if not player.strip() or ":" in player:
        raise UsageError("simulate needs a player name without ':'")
    rng = random.Random(seed)
    prefix = f"{game_date.isoformat()} " if game_date else ""
    lines = []
    for _ in range(games):
        tokens = [_random_frame(rng, number, strike_rate, spare_rate) for number in range(1, FRAMES + 1)]
        lines.append(f"{prefix}{player.strip()}: {' '.join(tokens)}")
    return lines


# ---------------------------------------------------------------- commands


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None


def _fraction(text: str) -> Fraction:
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None


def _rate(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("rate must be between 0 and 1")
    return value


def _percent(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("percent must be between 1 and 100")
    return value


def _filtered(games: list[Game], args: argparse.Namespace) -> list[Game]:
    return select_games(games, args.player, args.since, args.until)


def _game_on_line(games: list[Game], problems: list[Problem], line: int) -> Game:
    for game in games:
        if game.line == line:
            return game
    for problem in problems:
        if problem.line == line and problem.level == "error":
            raise UsageError(f"line {line} is not a valid game: {problem.message}")
    raise UsageError(f"no game on line {line}")


def cmd_check(args: argparse.Namespace) -> int:
    games, problems = load_league(args.file)
    errors = [problem for problem in problems if problem.level == "error"]
    warnings = [problem for problem in problems if problem.level == "warning"]
    if args.json:
        _emit_json([{"line": p.line, "level": p.level, "message": p.message} for p in problems])
    else:
        for problem in problems:
            print(f"{args.file}:{problem.line}: {problem.level}: {problem.message}")
        print(f"{len(games)} game(s), {len(errors)} error(s), {len(warnings)} warning(s)")
    failed = bool(errors) or (args.strict and bool(warnings))
    return 1 if failed else 0


def _score_row(game: Game) -> dict:
    total = game_total(game)
    return {
        "line": game.line,
        "date": game.game_date.isoformat() if game.game_date else "",
        "player": game.player,
        "frames": f"{len(game.frames)}/{FRAMES}",
        "complete": game.complete,
        "total": total,
        "so_far": scored_so_far(game),
    }


def cmd_score(args: argparse.Namespace) -> int:
    games, _ = load_league(args.file)
    rows = [_score_row(game) for game in _filtered(games, args)]
    if args.json:
        _emit_json(rows)
        return 0
    if not rows:
        print("no games match")
        return 0
    shown = [
        {**row, "date": row["date"] or "-",
         "total": row["total"] if row["total"] is not None else f"({row['so_far']} so far)"}
        for row in rows
    ]
    print(_table(shown, [("line", "line"), ("date", "date"), ("player", "player"),
                         ("frames", "frames"), ("total", "total")]))
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    games, problems = load_league(args.file)
    print(render_card(_game_on_line(games, problems, args.line)))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    games, problems = load_league(args.file)
    game = _game_on_line(games, problems, args.line)
    print(f"{game.player} (line {game.line})")
    for line in explain_game(game):
        print(line)
    return 0


def _series_row(stats: PlayerStats, base: int, percent: int) -> dict:
    count = len(stats.scores)
    series = sum(stats.scores)
    average = Fraction(series, count)
    hcp = handicap(average, base, percent)
    return {
        "player": stats.player,
        "games": count,
        "series": series,
        "average": _fmt(average),
        "high": max(stats.scores),
        "low": min(stats.scores),
        "handicap": hcp,
        "handicap_series": series + hcp * count,
        "strike_pct": _pct(stats.strikes, stats.first_balls),
        "spare_pct": _pct(stats.spares, stats.spare_chances),
        "open": stats.opens,
        "gutters": stats.gutters,
    }


def cmd_series(args: argparse.Namespace) -> int:
    games, _ = load_league(args.file)
    selected = _filtered(games, args)
    rows = [_series_row(stats, args.base, args.percent) for stats in player_stats(selected)]
    if args.json:
        _emit_json(rows)
        return 0
    if not rows:
        print("no complete games match")
        return 0
    print(_table(rows, [("player", "player"), ("games", "games"), ("series", "series"),
                        ("average", "avg"), ("high", "high"), ("low", "low"),
                        ("handicap", "hcp"), ("handicap_series", "hcp series"),
                        ("strike_pct", "strike %"), ("spare_pct", "spare %"),
                        ("open", "open"), ("gutters", "gutters")]))
    incomplete = sum(1 for game in selected if not game.complete)
    if incomplete:
        print(f"{incomplete} in-progress game(s) left out of averages")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    games, problems = load_league(args.file)
    errors = [problem for problem in problems if problem.level == "error"]
    if errors:
        for problem in errors:
            print(f"{args.file}:{problem.line}: error: {problem.message}")
        return 2
    stats_list = player_stats(_filtered(games, args))
    if not stats_list:
        print("no complete games to gate")
        return 1
    ok = True
    for stats in stats_list:
        average = Fraction(sum(stats.scores), len(stats.scores))
        passed = average >= args.min_average
        ok = ok and passed
        verdict = "PASS" if passed else "FAIL"
        print(f"{stats.player}: average {_fmt(average)} vs minimum {_fmt(args.min_average)}: {verdict}")
    return 0 if ok else 1


def cmd_simulate(args: argparse.Namespace) -> int:
    lines = simulate_lines(args.player, args.games, args.seed, args.strike_rate,
                           args.spare_rate, args.date)
    for line in lines:
        print(line)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    games, _ = load_league(args.file)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["line", "date", "player", "frames", "complete", "total", "so_far", "tokens"])
    for game in _filtered(games, args):
        writer.writerow([
            game.line,
            game.game_date.isoformat() if game.game_date else "",
            game.player,
            len(game.frames),
            "yes" if game.complete else "no",
            "" if game_total(game) is None else game_total(game),
            scored_so_far(game),
            " ".join(game.tokens),
        ])
    print(buffer.getvalue(), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tenpin",
        description="Offline ten-pin bowling scorer for plain-text league files.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def filters(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--player", help="only games by this player (case-insensitive)")
        sub.add_argument("--since", type=_iso_date, help="games on or after YYYY-MM-DD")
        sub.add_argument("--until", type=_iso_date, help="games on or before YYYY-MM-DD")

    check = commands.add_parser("check", help="lint a league file with line-numbered errors")
    check.add_argument("file")
    check.add_argument("--strict", action="store_true", help="treat warnings as failures")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=cmd_check)

    score = commands.add_parser("score", help="score every game")
    score.add_argument("file")
    filters(score)
    score.add_argument("--json", action="store_true")
    score.set_defaults(handler=cmd_score)

    card = commands.add_parser("card", help="print the ASCII scorecard for one line")
    card.add_argument("file")
    card.add_argument("--line", type=int, required=True, help="line number of the game")
    card.set_defaults(handler=cmd_card)

    explain = commands.add_parser("explain", help="show how each frame was scored")
    explain.add_argument("file")
    explain.add_argument("--line", type=int, required=True, help="line number of the game")
    explain.set_defaults(handler=cmd_explain)

    series = commands.add_parser("series", help="per-player averages, handicaps and stats")
    series.add_argument("file")
    filters(series)
    series.add_argument("--base", type=int, default=DEFAULT_BASE, help="handicap base (default 210)")
    series.add_argument("--percent", type=_percent, default=DEFAULT_PERCENT,
                        help="handicap percentage (default 90)")
    series.add_argument("--json", action="store_true")
    series.set_defaults(handler=cmd_series)

    gate = commands.add_parser("gate", help="fail (exit 1) if averages fall below a minimum")
    gate.add_argument("file")
    filters(gate)
    gate.add_argument("--min-average", type=_fraction, required=True)
    gate.set_defaults(handler=cmd_gate)

    simulate = commands.add_parser("simulate", help="print seeded random games in file format")
    simulate.add_argument("--player", required=True)
    simulate.add_argument("--games", type=int, default=1)
    simulate.add_argument("--seed", type=int, default=0)
    simulate.add_argument("--strike-rate", type=_rate, default=0.25)
    simulate.add_argument("--spare-rate", type=_rate, default=0.5)
    simulate.add_argument("--date", type=_iso_date, help="date to put on every game")
    simulate.set_defaults(handler=cmd_simulate)

    export = commands.add_parser("export", help="write every game as CSV to stdout")
    export.add_argument("file")
    filters(export)
    export.set_defaults(handler=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, UnicodeDecodeError, UsageError) as exc:
        print(f"tenpin: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
