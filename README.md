# AHA Women's Hockey Explorer

An interactive SQL explorer for Atlantic Hockey Association women's hockey statistics, scraped from [atlantichockeyamerica.com](https://atlantichockeyamerica.com/stats.aspx?path=whockey&year=2025).

Scrapes the full 2025-26 season including individual stat leaderboards, team game results, and all box scores (scoring summaries, skater stats, goalie stats, power plays, and penalties).

## Data available

| Table | Description |
|---|---|
| `teams` | Conference team standings and summary stats |
| `skater_season` | Individual player season leaderboards (goals, assists, points, etc.) |
| `goalie_season` | Individual goalie season stats (GAA, save %, shutouts, saves per game) |
| `game_results` | Team-by-team game-by-game results for the full season |
| `scoring` | Goal-by-goal scoring summary from every box score |
| `skater_game` | Player-level box score stats for every game (goals, assists, shots, blocks, etc.) |
| `goalie_game` | Goalie box score stats for every game (saves by period, GA, minutes) |
| `power_plays` | Power play summary for each game |
| `penalties` | Penalty log for every game |

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

Data loads in about 2–3 minutes (scrapes ~160 box scores). Once loaded you'll see a `sql>` prompt. Type any SQL query, `schema <table>` to see columns, or `quit` to exit.

## Example queries

Top goal scorers:
```sql
SELECT Player, Team, "No." as Goals FROM skater_season WHERE StatCategory = 'Goals' ORDER BY Goals DESC LIMIT 10
```

Goalie leaderboard by GAA (min 10 GP):
```sql
SELECT Player, Team, GP, GAA, SavePct, SO FROM goalie_season WHERE GP >= 10 ORDER BY GAA
```

Shots allowed per game by team:
```sql
WITH per_game AS (SELECT gg.Game, SUM(gg.Total + gg.GA) as shots_allowed FROM goalie_game gg JOIN goalie_season gs ON gg.Player = gs.Player WHERE (gg.Total > 0 OR gg.GA > 0) GROUP BY gg.Game, gs.Team) SELECT gs.Team, COUNT(*) as games, ROUND(AVG(CAST(shots_allowed AS FLOAT)), 2) as avg_shots_allowed FROM per_game JOIN goalie_season gs ON 1=1 GROUP BY gs.Team ORDER BY avg_shots_allowed
```

Blocked shots per game by team:
```sql
WITH per_game AS (SELECT regexp_replace(BoxTeam, '\s+\d+$', '') as Team, Game, SUM(Blk) as blocks FROM skater_game WHERE Blk IS NOT NULL GROUP BY Team, Game) SELECT Team, COUNT(*) as games, ROUND(AVG(CAST(blocks AS FLOAT)), 2) as avg_blocks_per_game FROM per_game GROUP BY Team ORDER BY avg_blocks_per_game DESC
```

Delaware shots allowed and blocked shots per game:
```sql
WITH shots AS (SELECT gg.Game, SUM(gg.Total + gg.GA) as shots_allowed FROM goalie_game gg JOIN goalie_season gs ON gg.Player = gs.Player WHERE gs.Team = 'DEL' AND (gg.Total > 0 OR gg.GA > 0) GROUP BY gg.Game), blocks AS (SELECT Game, SUM(Blk) as blocked_shots FROM skater_game WHERE regexp_replace(BoxTeam, '\s+\d+$', '') = 'Delaware' AND Blk IS NOT NULL GROUP BY Game) SELECT s.Game, s.shots_allowed, b.blocked_shots FROM shots s LEFT JOIN blocks b ON s.Game = b.Game ORDER BY s.Game
```

Goals by period across all games:
```sql
SELECT Per, COUNT(*) as goals FROM scoring WHERE Per IS NOT NULL GROUP BY Per ORDER BY Per
```

## Notes

- Data is scraped live each time the script runs (~2–3 minutes)
- Box scores are sourced from individual game pages; ~160 games scraped for the 2025-26 season
- Game labels in box score tables are unique per contest ID, so repeat matchups between the same teams are distinguishable
