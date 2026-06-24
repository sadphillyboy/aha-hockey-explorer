import urllib.request
import pandas as pd
import duckdb
import re
from io import StringIO
from bs4 import BeautifulSoup

BASE    = "https://atlantichockeyamerica.com"
STATS   = f"{BASE}/stats.aspx?path=whockey&year=2025"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


# ── helpers ──────────────────────────────────────────────────────────────────

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req).read().decode("utf-8")

def tables_from(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for t in soup.find_all("table"):
        h = t.find_previous(["h1","h2","h3","h4","h5"])
        try:
            df = pd.read_html(StringIO(str(t)))[0]
            # Flatten multi-level column headers
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [
                    f"{a}_{b}" if str(b) not in ("", str(a)) and "Unnamed" not in str(a)
                    else (b if "Unnamed" in str(a) else a)
                    for a, b in df.columns
                ]
            df.columns = [str(c) for c in df.columns]
            out.append((h.get_text(strip=True) if h else "", df))
        except Exception:
            pass
    return out

def parse_player_team(s):
    """
    'Janecke, Tessa (Penn State)' -> ('Janecke, Tessa', 'Penn State')
    'Bellina, Sophia - RIT'       -> ('Bellina, Sophia', 'RIT')
    """
    s = str(s).strip()
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # Dash format: "Last, First - Team" — only split on " - " when a comma precedes it
    if "," in s and " - " in s:
        idx = s.rfind(" - ")
        return s[:idx].strip(), s[idx + 3:].strip()
    return s, None


# ── main stats page ───────────────────────────────────────────────────────────

def scrape_main():
    html  = fetch(STATS)
    soup  = BeautifulSoup(html, "lxml")
    tbls  = tables_from(html)

    # Team summary (first table)
    teams = tbls[0][1].dropna(subset=["Team"])
    teams = teams[~teams["Team"].isin(["Team"])]

    # Individual player leaderboard tables (tables with a "Player" column
    # that contains "(TeamName)" entries)
    skater_frames = []
    goalie_by_cat = {}   # heading -> DataFrame, kept separate for merging
    goalie_headings = {"Goals Against AVG", "Saves Per Game", "Shutouts", "Saves Percentage"}

    for heading, df in tbls:
        if "Player" not in df.columns:
            continue
        # Drop index / header repeat rows
        df = df[df["Player"].notna()]
        df = df[~df["Player"].astype(str).str.strip().isin(["Player", "Index", "nan"])]
        if df.empty:
            continue

        df = df.copy()
        df[["Player","Team"]] = df["Player"].apply(
            lambda x: pd.Series(parse_player_team(x))
        )
        df.drop(columns=["Index"], errors="ignore", inplace=True)

        if heading in goalie_headings or \
           any(c in df.columns for c in ["GA/AVG","Shutouts","Sho/G","PCT"]):
            goalie_by_cat[heading] = df
        else:
            df["StatCategory"] = heading
            skater_frames.append(df)

    skater_season = pd.concat(skater_frames, ignore_index=True) if skater_frames else pd.DataFrame()

    # Merge the 4 goalie leaderboard tables into one row per player
    if goalie_by_cat:
        col_renames = {
            "Goals Against AVG": {"GA/AVG": "GAA"},
            "Saves Per Game":    {"AVG/G": "SavesPerGame"},
            "Shutouts":          {"Shutouts": "SO", "Sho/G": "SOPerGame"},
            "Saves Percentage":  {"PCT": "SavePct"},
        }
        merged = None
        for cat, df in goalie_by_cat.items():
            df = df.rename(columns=col_renames.get(cat, {}))
            if merged is None:
                merged = df
            else:
                shared = [c for c in df.columns if c in merged.columns
                          and c not in ("Player", "Team")]
                merged = merged.merge(df, on=["Player", "Team"], how="outer",
                                      suffixes=("", "_r"))
                for col in shared:
                    if col + "_r" in merged.columns:
                        merged[col] = merged[col].combine_first(merged[col + "_r"])
                        merged.drop(columns=[col + "_r"], inplace=True)
        # Coalesce any remaining duplicates (same player, team parsed differently)
        goalie_season = merged.groupby("Player", as_index=False).first()
    else:
        goalie_season = pd.DataFrame()

    # Per-team game logs (heading contains team name + record)
    game_results_frames = []
    teams_list = teams["Team"].tolist()
    for heading, df in tbls:
        if any(team in heading for team in teams_list) and "Date" in df.columns:
            df = df.copy()
            df["Team"] = re.sub(r"\s*\(.*?\)", "", heading).strip()
            df = df[df["Date"].notna()]
            df = df[~df["Date"].astype(str).str.strip().isin(["Date"])]
            game_results_frames.append(df)

    game_results = pd.concat(game_results_frames, ignore_index=True) if game_results_frames else pd.DataFrame()

    # Box score URLs
    boxscore_urls = list(dict.fromkeys(
        a["href"] for a in soup.find_all("a", href=True) if "boxscore.aspx" in a["href"]
    ))

    return teams, skater_season, goalie_season, game_results, boxscore_urls


# ── box score scraper ─────────────────────────────────────────────────────────

def scrape_boxscore(url):
    html = fetch(url)
    tbls = tables_from(html)
    result = {"scoring": None, "skaters": [], "goalies": [],
              "power_plays": [], "penalties": None}

    for heading, df in tbls:
        h = heading.lower()

        # Score line / game id — extract teams from first table heading
        if "-VS-" in heading and result.get("game_id") is None:
            m = re.match(r"^(.*?)\(.*?\)-VS-(.*?)\(", heading)
            if m:
                result["home"] = m.group(1).strip()
                result["away"] = m.group(2).strip()

        # Scoring summary
        if "Scoring Summary" in heading and result["scoring"] is None:
            df = df.copy()
            df.columns = [str(c) for c in df.columns]
            keep = [c for c in df.columns if c not in ("Logo","Per./Time","Unnamed: 1")]
            result["scoring"] = df[keep].dropna(how="all")

        # Skater box score (heading like "Penn State 3" or "Delaware 1")
        elif re.match(r"^[A-Za-z ]+\d+$", heading) and "Player" in df.columns:
            df = df.copy()
            # Drop duplicate header rows
            df = df[df["Player"].astype(str) != "Player"]
            df = df[df["Player"].notna()]
            df.insert(0, "BoxTeam", heading)
            result["skaters"].append(df)

        # Goalkeeping
        elif "Goalkeeping" in heading and "Player" in df.columns:
            df = df.copy()
            df = df[df["Player"].notna()]
            df = df[~df["Player"].astype(str).isin(["Player", "TMTEAM"])]
            df["Player"] = df["Player"].astype(str).str.replace(r"^\d+", "", regex=True)
            result["goalies"].append(df)

        # Power play summary
        elif "Power Play" in heading:
            df = df.copy()
            df.insert(0, "PP_Team", heading)
            result["power_plays"].append(df)

        # Penalty summary
        elif "Penalty Summary" in heading and result["penalties"] is None:
            result["penalties"] = df.dropna(how="all")

    return result


# ── build database ────────────────────────────────────────────────────────────

def build_db():
    print("Fetching AHA conference stats page...")
    teams, skater_season, goalie_season, game_results, boxscore_urls = scrape_main()
    print(f"  {len(teams)} teams | {len(skater_season)} skater stat rows | "
          f"{len(goalie_season)} goalie stat rows | {len(game_results)} game results")

    print(f"\nScraping {len(boxscore_urls)} box scores...")
    all_scoring    = []
    all_skaters    = []
    all_goalies    = []
    all_power_plays = []
    all_penalties  = []

    for i, rel_url in enumerate(boxscore_urls):
        url = f"{BASE}/{rel_url}"
        try:
            bs = scrape_boxscore(url)
            game_id    = re.search(r"ContestID=(\w+)", rel_url)
            game_label = f"{bs.get('home','?')} vs {bs.get('away','?')}" + \
                         (f" [{game_id.group(1)}]" if game_id else f" [#{i}]")

            if bs["scoring"] is not None:
                df = bs["scoring"].copy()
                df.insert(0, "Game", game_label)
                all_scoring.append(df)

            for df in bs["skaters"]:
                df = df.copy()
                df.insert(0, "Game", game_label)
                all_skaters.append(df)

            for df in bs["goalies"]:
                df = df.copy()
                df.insert(0, "Game", game_label)
                all_goalies.append(df)

            for df in bs["power_plays"]:
                df = df.copy()
                df.insert(0, "Game", game_label)
                all_power_plays.append(df)

            if bs["penalties"] is not None:
                df = bs["penalties"].copy()
                df.insert(0, "Game", game_label)
                all_penalties.append(df)

            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(boxscore_urls)}] done")

        except Exception as e:
            print(f"  Warning: failed {rel_url[:40]}... — {e}")

    print(f"  [{len(boxscore_urls)}/{len(boxscore_urls)}] done\n")

    def safe_concat(frames):
        if not frames:
            return pd.DataFrame({"_empty": []})
        return pd.concat(frames, ignore_index=True)

    scoring     = safe_concat(all_scoring)
    skater_game = safe_concat(all_skaters)
    goalie_game = safe_concat(all_goalies)
    power_plays = safe_concat(all_power_plays)
    penalties   = safe_concat(all_penalties)

    con = duckdb.connect()
    for name, df in [
        ("teams",         teams),
        ("skater_season", skater_season),
        ("goalie_season", goalie_season),
        ("game_results",  game_results),
        ("scoring",       scoring),
        ("skater_game",   skater_game),
        ("goalie_game",   goalie_game),
        ("power_plays",   power_plays),
        ("penalties",     penalties),
    ]:
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")

    print("Tables loaded:")
    print(f"  teams:         {len(teams)} rows")
    print(f"  skater_season: {len(skater_season)} rows")
    print(f"  goalie_season: {len(goalie_season)} rows")
    print(f"  game_results:  {len(game_results)} rows")
    print(f"  scoring:       {len(scoring)} rows  (goal-by-goal)")
    print(f"  skater_game:   {len(skater_game)} rows  (player box scores)")
    print(f"  goalie_game:   {len(goalie_game)} rows  (goalie box scores)")
    print(f"  power_plays:   {len(power_plays)} rows")
    print(f"  penalties:     {len(penalties)} rows")
    print()
    return con


# ── SQL REPL ──────────────────────────────────────────────────────────────────

def repl(con):
    TABLE_LIST = ["teams","skater_season","goalie_season","game_results",
                  "scoring","skater_game","goalie_game","power_plays","penalties"]
    print("AHA Women's Hockey SQL Explorer — 2025-26 season")
    print("Tables:", ", ".join(TABLE_LIST))
    print("Type 'schema <table>' to see columns, or 'quit' to exit.\n")
    print("Example: SELECT Player, Team, \"No.\" as Goals FROM skater_season WHERE StatCategory = 'Goals' ORDER BY Goals DESC LIMIT 10\n")

    while True:
        try:
            query = input("sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break

        if query.lower().startswith("schema"):
            parts = query.split()
            table = parts[1] if len(parts) > 1 else None
            if not table:
                print("Usage: schema <table>  (tables: " + ", ".join(TABLE_LIST) + ")\n")
                continue
            try:
                result = con.execute(f"DESCRIBE {table}").fetchdf()
                cols = result[["column_name","column_type"]].values.tolist()
                w = max(len(c[0]) for c in cols)
                for name, typ in cols:
                    print(f"  {name:<{w}}  {typ}")
                print()
            except Exception as e:
                print(f"Error: {e}\n")
            continue

        try:
            df = con.execute(query).fetchdf()
            print(df.to_string(index=False))
            print(f"\n({len(df)} rows)\n")
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    con = build_db()
    repl(con)
