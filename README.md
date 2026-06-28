# AHA Women's Hockey Explorer

An interactive SQL explorer for Atlantic Hockey Association women's hockey statistics, scraped from [atlantichockeyamerica.com](https://atlantichockeyamerica.com/stats.aspx?path=whockey&year=2025).

Scrapes the full 2025-26 season including individual stat leaderboards, team game results, and all box scores (scoring summaries, skater stats, goalie stats, power plays, penalties, and play-by-play).

## Data available

| Table | Description |
|---|---|
| `teams` | Conference team standings and summary stats |
| `skater_season` | Individual player season leaderboards — one row per player (goals, assists, points, shots, PPG, SHG, GWG, faceoffs, etc.) |
| `goalie_season` | Individual goalie season stats (GAA, save %, shutouts, saves per game) |
| `game_results` | Team-by-team game-by-game results for the full season |
| `scoring` | Goal-by-goal scoring summary from every box score |
| `skater_game` | Player-level box score stats for every game (goals, assists, shots, blocks, etc.) |
| `goalie_game` | Goalie box score stats for every game (saves by period, GA, minutes) |
| `power_plays` | Power play summary for each game with correct team attribution |
| `penalties` | Penalty log for every game |
| `play_by_play` | Every event from every game — shots, goals, penalties, faceoffs with parsed fields |

## play_by_play columns

| Column | Description |
|---|---|
| `Game` | Game label (HomeTeam vs AwayTeam [ContestID]) |
| `Period` | Period number (1–4+, where 4+ = OT) |
| `Time` | Timestamp within period (MM:SS) |
| `EventType` | Shot, Goal, Penalty, Faceoff, PowerPlayStart, PowerPlayEnd, GoalieChange, etc. |
| `Team` | Team name (normalized — see below) |
| `Player` | Primary player involved |
| `ShotOutcome` | SAVE, WIDE, or BLOCKED — note: the site labels saves as "MISSED"; corrected here |
| `SavedBy` | Goalie name on saves |
| `BlockedBy` | Player name on blocked shots |
| `GoalType` | POWER-PLAY, EVEN STRENGTH, OVERTIME, GAME WINNER, etc. |
| `Assists` | Assist players (goals only) |
| `PenaltyMinutes` | Penalty duration in minutes |
| `PenaltyInfraction` | Penalty type (Interference, Tripping, etc.) |
| `FaceoffWinner` | Team that won the faceoff |
| `RawText` | Original play-by-play text |

## Team name normalization

The AHA website uses inconsistent abbreviations and full names across tables. All team names in `play_by_play` are normalized to a single canonical name via `TEAM_NORM`. Key examples:

| Canonical Name | Raw variants found in source |
|---|---|
| Mercyhurst | MU, MER, MHU, MH |
| Lindenwood | LIN, LWU, LDW, LU |
| Delaware | DEL, UD |
| Penn State | PSU, Penn St., Penn State |
| Merrimack | MCK |
| UConn | CON, UOC |

## Requirements

- Python 3.9+
- pip

## Installation

```bash
pip3 install pandas duckdb lxml beautifulsoup4
```

## Usage

```bash
python3 aha_explorer.py
```

Data loads in about 2–3 minutes (scrapes ~160 box scores). Once loaded you'll see a `sql>` prompt. The REPL supports multi-line queries — end your query with a semicolon to run it. Type `schema <table>` to see columns, or `quit` to exit.

## Example queries

Top goal scorers:
```sql
SELECT Player, Team, Goals FROM skater_season ORDER BY Goals DESC LIMIT 10;
```

Goalie leaderboard by GAA (min 10 GP):
```sql
SELECT Player, Team, GP, GAA, SavePct, SO FROM goalie_season WHERE GP >= 10 ORDER BY GAA;
```

Wide shot percentage by AHA team:
```sql
SELECT Team, COUNT(*) AS TotalShots,
    SUM(CASE WHEN ShotOutcome = 'WIDE' THEN 1 ELSE 0 END) AS WideShots,
    ROUND(100.0 * SUM(CASE WHEN ShotOutcome = 'WIDE' THEN 1 ELSE 0 END) / COUNT(*), 1) AS WidePct
FROM play_by_play
WHERE EventType = 'Shot' AND Team IN (SELECT Team FROM teams)
GROUP BY Team ORDER BY WidePct DESC;
```

Goals and shots on net by period:
```sql
SELECT Team,
    SUM(CASE WHEN Period = 1 THEN 1 ELSE 0 END) AS P1_ShotsOnNet,
    SUM(CASE WHEN Period = 2 THEN 1 ELSE 0 END) AS P2_ShotsOnNet,
    SUM(CASE WHEN Period = 3 THEN 1 ELSE 0 END) AS P3_ShotsOnNet,
    SUM(CASE WHEN Period = 1 AND EventType = 'Goal' THEN 1 ELSE 0 END) AS P1_Goals,
    SUM(CASE WHEN Period = 2 AND EventType = 'Goal' THEN 1 ELSE 0 END) AS P2_Goals,
    SUM(CASE WHEN Period = 3 AND EventType = 'Goal' THEN 1 ELSE 0 END) AS P3_Goals
FROM play_by_play
WHERE Team IN (SELECT Team FROM teams)
  AND ((EventType = 'Shot' AND ShotOutcome = 'SAVE') OR EventType = 'Goal')
GROUP BY Team ORDER BY Team;
```

Poisson probability of scoring at least one goal per period:
```sql
WITH gp AS (
    SELECT Team, COUNT(DISTINCT Game) AS GP FROM play_by_play
    WHERE Team IN (SELECT Team FROM teams) GROUP BY Team
),
goals AS (
    SELECT Team,
        SUM(CASE WHEN Period = 1 THEN 1 ELSE 0 END) AS Goals_P1,
        SUM(CASE WHEN Period = 2 THEN 1 ELSE 0 END) AS Goals_P2,
        SUM(CASE WHEN Period = 3 THEN 1 ELSE 0 END) AS Goals_P3
    FROM play_by_play
    WHERE EventType = 'Goal' AND Team IN (SELECT Team FROM teams) AND Period <= 3
    GROUP BY Team
),
lambdas AS (
    SELECT g.Team, gp.GP,
        Goals_P1 * 1.0 / gp.GP AS L1,
        Goals_P2 * 1.0 / gp.GP AS L2,
        Goals_P3 * 1.0 / gp.GP AS L3
    FROM goals g JOIN gp ON g.Team = gp.Team
)
SELECT Team, GP,
    ROUND(1 - exp(-L1), 3) AS P1_AtLeast1Goal,
    ROUND(1 - exp(-L2), 3) AS P2_AtLeast1Goal,
    ROUND(1 - exp(-L3), 3) AS P3_AtLeast1Goal
FROM lambdas ORDER BY Team;
```

Offensive clustering metric (OffScore) for forwards — min 7 GP:
```sql
SELECT s.Player, s.BoxTeam AS Team, COUNT(*) AS GP,
    SUM(G) AS Goals, SUM(A) AS Assists, SUM(Total) AS Shots,
    ROUND((SUM(G) * (SUM(Total) / (SUM(Total) + 20.0)) * 3
           + SUM(A) + SUM(Total) * 0.15) / COUNT(*), 3) AS OffScore
FROM skater_game s
WHERE s.BoxTeam IN (SELECT Team FROM teams) AND s.Pos = 'O'
GROUP BY s.Player, s.BoxTeam
HAVING COUNT(*) >= 7
ORDER BY OffScore DESC;
```

## Notes

- Data is scraped live each time the script runs (~2–3 minutes)
- The site mislabels saved shots as "MISSED" in play-by-play — corrected to SAVE in this scraper; "WIDE" means the shot missed the net
- Team names are normalized at load time via `TEAM_NORM` to fix inconsistent abbreviations across games
- `skater_season` is pivoted from long to wide format — one row per player with all stat categories as columns
- Power play team attribution is derived from the VS heading (home team first, away team second) since PP tables appear before skater tables in the HTML
- Box scores are sourced from individual game pages; ~160 games scraped for the 2025-26 season
- Game labels use the format `HomeTeam vs AwayTeam [ContestID]` — use `regexp_extract` to parse home/away from this column
