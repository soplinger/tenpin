# Tenpin

**Offline ten-pin bowling scorer for plain-text league files.** Write each game as one line of frame notation, and Tenpin scores it exactly, explains every frame, lints the file with line numbers, and works out averages, handicaps and strike and spare stats. It runs on Python 3's standard library, with no accounts, servers or network calls.

## Features

- **Exact scoring** of strikes, spares, open frames and the tenth-frame fill balls, using standard USBC frame rules.
- **Plain-text league files**: one game per line, with an optional ISO date and `#` comments.
- **Line-numbered lint** (`check`) that catches bad symbols, impossible pin counts, misplaced bonus rolls, too many frames and unfinished frames, with `--strict` and `--json` options.
- **Duplicate and in-progress warnings** so a double-entered game or a half-finished game is visible.
- **Running totals** that wait for pending bonuses, plus a `so far` figure for games still in progress.
- **ASCII scorecard** (`card`) with frame numbers, rolls and cumulative totals.
- **Frame-by-frame explanation** (`explain`) that shows each strike and spare bonus as arithmetic.
- **Per-player series** (`series`) with games, series total, average, high and low.
- **USBC handicaps** worked out from the average (90% of the gap to 210, rounded down), with a configurable base and percentage, plus handicap series.
- **Strike %, spare conversion, open frames and gutter balls** for every player, from complete games only.
- **Filters** by player (`--player`) and date range (`--since`, `--until`) on score, series, gate and export.
- **Average gate** (`gate`) that exits 1 when a player's average falls below a minimum, for use in CI or a league-night check.
- **Seeded simulator** (`simulate`) that writes legal random games in file format, repeatable from a seed for demos and test data.
- **CSV export** (`export`) of every game for spreadsheets.
- **JSON output** on `check`, `score` and `series` for scripts.

## Quick start

The repository includes a sample league in `examples/league.txt`.

```sh
python3 tenpin.py check examples/league.txt
```

```text
examples/league.txt:8: warning: game in progress: tenth frame still needs a roll
6 game(s), 0 error(s), 1 warning(s)
```

```sh
python3 tenpin.py score examples/league.txt
```

```text
line  date        player  frames  total
----  ----------  ------  ------  ------------
3     2026-10-08  Ana     10/10   167
4     2026-10-08  Ben     10/10   127
5     2026-10-08  Cy      10/10   147
6     2026-10-15  Ana     10/10   147
7     2026-10-15  Ben     10/10   154
8     2026-10-15  Cy      10/10   (114 so far)
```

```sh
python3 tenpin.py explain examples/league.txt --line 3
```

```text
Ana (line 3)
frame  1  X    strike: 10 + 7 + 3 = 20  running 20
frame  2  7/   spare: 10 + 9 = 19  running 39
frame  3  9-   open: 9 + 0 = 9  running 48
frame  4  X    strike: 10 + 0 + 8 = 18  running 66
frame  5  -8   open: 0 + 8 = 8  running 74
frame  6  8/   spare: 10 + 0 = 10  running 84
frame  7  -6   open: 0 + 6 = 6  running 90
frame  8  X    strike: 10 + 10 + 10 = 30  running 120
frame  9  X    strike: 10 + 10 + 8 = 28  running 148
frame 10  X81  tenth: 10 + 8 + 1 = 19  running 167
```

Add `--player Ana` or `--since 2026-10-15` to narrow any of these. The `card --line 3` command prints the same game as an ASCII scorecard.

## League file format

```text
# Thursday mixed league, week 42
2026-10-08 Ana: X 7/ 9- X -8 8/ -6 X X X81
2026-10-15 Ben: X X 9- 8- 7/ 9- X 8- 9/ 7/8
Cy: 7- 6- 9/ X 7/ 8- 6- 9- 8/ X
```

- Each line is `[YYYY-MM-DD] Player name: frame frame ...`. The date is optional, and blank lines and `#` comments are skipped.
- Frames 1 to 9 are one token each: `X` (strike), `7/` (spare), `9-` (open, `-` is a miss) or `45`.
- The tenth frame holds up to three rolls: `9-`, `X7/`, `XX8`.
- Lowercase is accepted. A game with fewer than ten frames is treated as in progress.

## Commands

| Command | What it does |
| --- | --- |
| `check FILE [--strict] [--json]` | Lint the file. Exit 0 when clean, 1 on errors (or warnings with `--strict`). |
| `score FILE [--player] [--since] [--until] [--json]` | Total for each game. |
| `card FILE --line N` | ASCII scorecard for the game on line N. |
| `explain FILE --line N` | Frame-by-frame scoring arithmetic. |
| `series FILE [--base 210] [--percent 90] [--json]` | Per-player averages, handicaps and stats. |
| `gate FILE --min-average N` | Exit 1 if any selected player's average is below N. |
| `simulate --player NAME [--games N] [--seed S] [--strike-rate R] [--spare-rate R] [--date D]` | Print seeded random games. |
| `export FILE` | Write every game as CSV to stdout. |

Exit codes: `0` success, `1` a failed check or gate, `2` a usage or file error.

## How to run

Requires Python 3.10 or newer. There are no dependencies to install.

```sh
python3 tenpin.py --help
python3 tenpin.py simulate --player Demo --games 3 --seed 7 --date 2026-10-22 > demo.txt
python3 tenpin.py series demo.txt
```

## How to test

The tests use pytest, which is the only extra dependency.

```sh
python3 -m pytest -q
```

The tests cover frame notation and its rule checks, scoring with bonuses, pending totals, handicaps, series statistics, simulation, scorecard and explanation output, and the CLI exit codes and filters.

## Project layout

```text
tenpin.py              the scorer and command-line interface (standard library only)
examples/league.txt    sample league file used in the quick start
tests/conftest.py      puts the project root on the import path for pytest
tests/test_tenpin.py   unit and CLI tests
```
