import urllib.request
import pandas as pd
import duckdb
import re
from io import StringIO
from bs4 import BeautifulSoup

BASE    = "https://atlantichockeyamerica.com"
STATS   = f"{BASE}/stats.aspx?path=whockey&year=2025"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# Normalize all team name variants (abbreviations + inconsistent full names) to one canonical name
TEAM_NORM = {
    # Mercyhurst — 4 abbreviations
    "MU": "Mercyhurst", "MER": "Mercyhurst", "MHU": "Mercyhurst", "MH": "Mercyhurst",
    "Mercyhurst": "Mercyhurst",
    # Delaware — 2 abbreviations
    "DEL": "Delaware", "UD": "Delaware",
    "Delaware": "Delaware",
    # Lindenwood — 4 abbreviations
    "LIN": "Lindenwood", "LWU": "Lindenwood", "LDW": "Lindenwood", "LU": "Lindenwood",
    "Lindenwood": "Lindenwood",
    # Merrimack
    "MCK": "Merrimack", "Merrimack": "Merrimack",
    # Penn State — 2 full name variants
    "PSU": "Penn State", "Penn St.": "Penn State", "Penn State": "Penn State",
    # Minnesota Duluth — 2 full name variants
    "UMD": "Minnesota Duluth", "Minn. Duluth": "Minnesota Duluth", "Minn. Duluth St.": "Minnesota Duluth",
    # Minnesota State — 3 variants
    "MSU": "Minnesota State", "Minnesota St.": "Minnesota State",
    "Minnesota State University": "Minnesota State",
    # UConn — 2 abbreviations
    "CON": "UConn", "UOC": "UConn", "UConn": "UConn",
    # Cornell — parsing artifact
    "COR": "Cornell", "Cornell": "Cornell", "Cornell Van": "Cornell",
    # Ohio State
    "OSU": "Ohio State", "Ohio St.": "Ohio State",
    # St. Cloud State
    "STC": "St. Cloud State", "St. Cloud St.": "St. Cloud State",
    # Single abbreviations
    "ASU": "Assumption",      "Assumption": "Assumption",
    "BRN": "Brown",           "Brown": "Brown",
    "BSU": "Bemidji State",   "Bemidji St.": "Bemidji State",
    "COL": "Colgate",         "Colgate": "Colgate",
    "HCC": "Holy Cross",      "Holy Cross": "Holy Cross",
    "LIU": "LIU",
    "NEU": "Northeastern",    "Northeastern": "Northeastern",
    "POS": "Post",            "Post": "Post",
    "QUI": "Quinnipiac",      "Quinnipiac": "Quinnipiac",
    "RIT": "RIT",
    "RMU": "Robert Morris",   "Robert Morris": "Robert Morris",
    "RPI": "Rensselaer",      "Rensselaer": "Rensselaer",
    "SLU": "St. Lawrence",    "St. Lawrence": "St. Lawrence",
    "STO": "Stonehill",       "Stonehill": "Stonehill",
    "SYR": "Syracuse",        "Syracuse": "Syracuse",
    "UNH": "New Hampshire",   "New Hampshire": "New Hampshire",
    "UNI": "Union",
    "UST": "St. Thomas",
    "UVM": "Vermont",         "Vermont": "Vermont",
    "WIS": "Wisconsin",       "Wisconsin": "Wisconsin",
    "YAL": "Yale",            "Yale": "Yale",
}


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

def _parse_pbp_row(period, time, text):
    ev = {
        'Period': period, 'Time': time, 'EventType': None,
        'Team': None, 'Player': None,
        'ShotOutcome': None, 'SavedBy': None, 'BlockedBy': None,
        'GoalType': None, 'Assists': None,
        'PenaltyMinutes': None, 'PenaltyInfraction': None,
        'FaceoffWinner': None, 'RawText': text,
    }

    # Shot — NOTE: site labels saves as "MISSED"; "WIDE" = missed the net
    m = re.match(r'Shot by (\S+) (.+?) (MISSED|WIDE|BLOCKED)(.*)', text)
    if m:
        ev['EventType'] = 'Shot'
        ev['Team']   = m.group(1)
        ev['Player'] = m.group(2).strip().rstrip(',')
        outcome, rest = m.group(3), m.group(4)
        if outcome == 'MISSED':
            ev['ShotOutcome'] = 'SAVE'
            s = re.search(r'save (.+)', rest)
            if s: ev['SavedBy'] = s.group(1).strip().rstrip('.')
        elif outcome == 'WIDE':
            ev['ShotOutcome'] = 'WIDE'
        else:
            ev['ShotOutcome'] = 'BLOCKED'
            b = re.search(r'by (.+)', rest)
            if b: ev['BlockedBy'] = b.group(1).strip().rstrip('.')
        return ev

    # Goal — "GOAL by Penn State Janecke, Tessa (POWER-PLAY, FIRST GOAL), Assist by ..."
    if text.startswith('GOAL by '):
        ev['EventType'] = 'Goal'
        rest  = text[8:]
        paren = rest.find('(')
        if paren != -1:
            team_player = rest[:paren].strip().rstrip(',')
            paren_end   = rest.find(')', paren)
            ev['GoalType'] = rest[paren + 1:paren_end].strip() if paren_end != -1 else None
            comma = team_player.find(',')
            if comma != -1:
                words = team_player[:comma].split()
                ev['Team']   = ' '.join(words[:-1])
                ev['Player'] = words[-1] + ',' + team_player[comma + 1:]
        a = re.search(r'Assist by (.+?)(?:, On ice|, goal|$)', text)
        if a: ev['Assists'] = a.group(1).strip()
        return ev

    # Penalty — "Penalty on Pieckenhagen, Charlotte WIS 2 minutes for Interference."
    m = re.match(r'Penalty on (.+?) (\S+) (\d+) minutes for (.+)', text)
    if m:
        ev['EventType']         = 'Penalty'
        ev['Player']            = m.group(1).strip()
        ev['Team']              = m.group(2)
        ev['PenaltyMinutes']    = int(m.group(3))
        ev['PenaltyInfraction'] = m.group(4).strip().rstrip('.')
        return ev

    # Faceoff — "Faceoff Janecke, Tessa vs Hall, Cassie won by WIS."
    m = re.match(r'Faceoff (.+?) vs .+ won by (\S+)', text)
    if m:
        ev['EventType']     = 'Faceoff'
        ev['Player']        = m.group(1).strip()
        ev['FaceoffWinner'] = m.group(2).rstrip('.')
        return ev

    if 'Start power play' in text:
        ev['EventType'] = 'PowerPlayStart'
        m = re.search(r'for (\S+)', text)
        if m: ev['Team'] = m.group(1).rstrip('.')
        return ev

    if 'End power play' in text:
        ev['EventType'] = 'PowerPlayEnd'
        m = re.search(r'for (\S+)', text)
        if m: ev['Team'] = m.group(1).rstrip('.')
        return ev

    if 'at goalie for' in text:
        ev['EventType'] = 'GoalieChange'
        m = re.match(r'(.+?) at goalie for (\S+)', text)
        if m:
            ev['Player'] = m.group(1).strip()
            ev['Team']   = m.group(2).rstrip('.')
        return ev

    if re.search(r'Start of .+ period', text, re.I):
        ev['EventType'] = 'PeriodStart'
        return ev

    if re.search(r'End of .+ period', text, re.I):
        ev['EventType'] = 'PeriodEnd'
        return ev

    if 'penalty complete' in text:
        ev['EventType'] = 'PenaltyComplete'
        m = re.match(r'(.+?) \((\S+)\) penalty complete', text)
        if m:
            ev['Player'] = m.group(1).strip()
            ev['Team']   = m.group(2)
        return ev

    if 'Timeout' in text:
        ev['EventType'] = 'Timeout'
        return ev

    ev['EventType'] = 'Other'
    return ev


def scrape_boxscore(url):
    html = fetch(url)
    tbls = tables_from(html)
    result = {"scoring": None, "skaters": [], "goalies": [],
              "power_plays": [], "penalties": None, "pbp": []}

    current_team = None
    pp_index     = 0
    home_team    = None
    away_team    = None
    for heading, df in tbls:
        h = heading.lower()

        # Score line / game id — extract teams from first table heading
        if "-VS-" in heading and home_team is None:
            m = re.match(r"^(.*?)\(.*?\)-VS-(.*?)\(", heading)
            if m:
                home_team        = m.group(1).strip()
                away_team        = m.group(2).strip()
                result["home"]   = home_team
                result["away"]   = away_team

        # Scoring summary
        if "Scoring Summary" in heading and result["scoring"] is None:
            df = df.copy()
            df.columns = [str(c) for c in df.columns]
            keep = [c for c in df.columns if c not in ("Logo","Per./Time","Unnamed: 1")]
            result["scoring"] = df[keep].dropna(how="all")

        # Skater box score (heading like "Penn State 3" or "Delaware 1")
        elif re.match(r"^[A-Za-z ]+\d+$", heading) and "Player" in df.columns:
            current_team = re.sub(r"\s*\d+$", "", heading).strip()
            df = df.copy()
            df = df[df["Player"].astype(str) != "Player"]
            df = df[df["Player"].notna()]
            df = df[~df["Player"].astype(str).str.strip().isin(["Total", "TMTEAM"])]
            df["Player"] = df["Player"].astype(str).str.replace(r"^\d+", "", regex=True).str.strip()
            df.insert(0, "BoxTeam", current_team)
            result["skaters"].append(df)

        # Goalkeeping
        elif "Goalkeeping" in heading and "Player" in df.columns:
            df = df.copy()
            df = df[df["Player"].notna()]
            df = df[~df["Player"].astype(str).isin(["Player", "TMTEAM"])]
            df["Player"] = df["Player"].astype(str).str.replace(r"^\d+", "", regex=True)
            df["Team"] = current_team
            result["goalies"].append(df)

        # Power play summary — PP tables appear before skater tables in HTML,
        # so use home/away order (first PP = home, second = away)
        elif "Power Play" in heading:
            df = df.copy()
            pp_team = home_team if pp_index == 0 else away_team
            df.insert(0, "PP_Team", pp_team)
            result["power_plays"].append(df)
            pp_index += 1

        # Penalty summary
        elif "Penalty Summary" in heading and result["penalties"] is None:
            result["penalties"] = df.dropna(how="all")

    # Play-by-play (lives in <section id="pbp-N">, not a standard table)
    soup_pbp = BeautifulSoup(html, "lxml")
    for period_num in range(1, 6):
        section = soup_pbp.find("section", id=f"pbp-{period_num}")
        if not section:
            break
        for tr in section.find_all("tr")[1:]:  # skip header row
            tds = tr.find_all("td")
            if not tds:
                continue
            raw = tds[-1].get_text(strip=True)  # last td always has full text
            if not raw:
                continue
            m = re.match(r"\[(\d+:\d+)\]\s*(.*)", raw, re.DOTALL)
            if not m:
                continue
            time_str, text = m.group(1), m.group(2).strip()
            if text:
                result["pbp"].append(_parse_pbp_row(period_num, time_str, text))

    return result


# ── build database ────────────────────────────────────────────────────────────

def pivot_skaters(df):
    base = df.groupby(['Player', 'Team'])['GP'].max().reset_index()

    def extract(cat, val_col, new_col):
        sub = df[df['StatCategory'] == cat][['Player', 'Team', val_col]]
        return sub.rename(columns={val_col: new_col})

    stats = [
        extract('Goals',              'No.',   'Goals'),
        extract('Assists',            'No.',   'Assists'),
        extract('Points',             'No.',   'Points'),
        extract('Shots',              'No.',   'Shots'),
        extract('Power Play Goals',   'No.',   'PPG'),
        extract('Short Handed Goals', 'No.',   'SHG'),
        extract('Game Winning Goals', 'No.',   'GWG'),
        extract('Hat Tricks',         'No.',   'HatTricks'),
        extract('Penalties',          'No.',   'Penalties'),
        extract('Blocked Shots',      'No.',   'BlockedShots'),
        extract('Plus / Minus',       '+/-',   'PlusMinus'),
        extract('Penalty Minutes',    'Min.',  'PIM'),
        extract('Goals Per Game',     'AVG/G', 'GoalsPerGame'),
        extract('Assists Per Game',   'AVG/G', 'AssistsPerGame'),
        extract('Points Per Game',    'AVG/G', 'PointsPerGame'),
        extract('Shots Per Game',     'AVG',   'ShotsPerGame'),
    ]

    fo = (df[df['StatCategory'] == 'Face-Off Wins/Total Face-Offs']
          [['Player', 'Team', 'Wins', 'Losses', 'Total']]
          .rename(columns={'Wins': 'FOWins', 'Losses': 'FOLosses', 'Total': 'FOTotal'}))
    fo_pct = (df[df['StatCategory'] == 'Face-Off Winning Percentage']
              [['Player', 'Team', 'PCT.']]
              .rename(columns={'PCT.': 'FOPct'}))

    result = base
    for s in stats:
        result = result.merge(s, on=['Player', 'Team'], how='left')
    result = result.merge(fo,     on=['Player', 'Team'], how='left')
    result = result.merge(fo_pct, on=['Player', 'Team'], how='left')
    return result


def build_db():
    print("Fetching AHA conference stats page...")
    teams, skater_season, goalie_season, game_results, boxscore_urls = scrape_main()
    skater_season = pivot_skaters(skater_season) if not skater_season.empty else skater_season
    print(f"  {len(teams)} teams | {len(skater_season)} skaters | "
          f"{len(goalie_season)} goalie stat rows | {len(game_results)} game results")

    print(f"\nScraping {len(boxscore_urls)} box scores...")
    all_scoring    = []
    all_skaters    = []
    all_goalies    = []
    all_power_plays = []
    all_penalties  = []
    all_pbp        = []

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

            if bs["pbp"]:
                pbp_df = pd.DataFrame(bs["pbp"])
                pbp_df.insert(0, "Game", game_label)
                all_pbp.append(pbp_df)

            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(boxscore_urls)}] done")

        except Exception as e:
            print(f"  Warning: failed {rel_url[:40]}... — {e}")

    print(f"  [{len(boxscore_urls)}/{len(boxscore_urls)}] done\n")

    def safe_concat(frames):
        if not frames:
            return pd.DataFrame({"_empty": []})
        return pd.concat(frames, ignore_index=True)

    scoring      = safe_concat(all_scoring)
    skater_game  = safe_concat(all_skaters)
    goalie_game  = safe_concat(all_goalies)
    power_plays  = safe_concat(all_power_plays)
    penalties    = safe_concat(all_penalties)
    play_by_play = safe_concat(all_pbp)
    if "Team" in play_by_play.columns:
        play_by_play["Team"] = play_by_play["Team"].map(
            lambda x: TEAM_NORM.get(x, x) if pd.notna(x) else x
        )
    if "FaceoffWinner" in play_by_play.columns:
        play_by_play["FaceoffWinner"] = play_by_play["FaceoffWinner"].map(
            lambda x: TEAM_NORM.get(x, x) if pd.notna(x) else x
        )

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
        ("play_by_play",  play_by_play),
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
    print(f"  play_by_play:  {len(play_by_play)} rows  (every event)")
    print()
    return con


# ── SQL REPL ──────────────────────────────────────────────────────────────────

def repl(con):
    TABLE_LIST = ["teams","skater_season","goalie_season","game_results",
                  "scoring","skater_game","goalie_game","power_plays","penalties","play_by_play"]
    print("AHA Women's Hockey SQL Explorer — 2025-26 season")
    print("Tables:", ", ".join(TABLE_LIST))
    print("Type 'schema <table>' to see columns, or 'quit' to exit.\n")
    print("Example: SELECT Player, Team, \"No.\" as Goals FROM skater_season WHERE StatCategory = 'Goals' ORDER BY Goals DESC LIMIT 10\n")

    while True:
        lines = []
        prompt = "sql> "
        while True:
            try:
                line = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                return
            lines.append(line)
            prompt = "  -> "
            combined = "\n".join(lines).strip()
            if not combined:
                lines = []
                prompt = "sql> "
                continue
            if combined.lower() in ("quit", "exit", "q"):
                print("Bye.")
                return
            if combined.lower().startswith("schema"):
                break
            if combined.rstrip().endswith(";"):
                break

        query = combined

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
