"""
Builds the team data file the site loads (data/fc26.js, data/fc27.js, ...).

    python tools/build_data.py fc27            # writes data/fc27.js
    python tools/build_data.py fc26 --label "FC 26"

Sources
  * OVR / ATT / MID / DEF  - fifaindex.com (club list, league pages, national team pages)
  * Star ratings           - fifagamenews.com/fc-XX-team-star-ratings
  * Teams missing from the star list get the star rating most common for their OVR
    among the teams that are on it, and are flagged as estimated (shown with "≈").

Pages are cached in tools/.cache so re-running doesn't refetch anything. FIFA Index
rate-limits, so requests are spaced out and retried; a full fresh build takes a while.
Requires the curl that ships with Git for Windows (the Windows one gets blocked).
"""
import argparse, collections, datetime, difflib, hashlib, html, json, os, re, shutil, subprocess, sys, time, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
CURL = next((p for p in [r"C:\Program Files\Git\mingw64\bin\curl.exe", shutil.which("curl")] if p and os.path.exists(p)), "curl")
UA = "Mozilla/5.0"
DELAY = 8

# National teams: name -> (EA team id on FIFA Index, flag code for flagcdn / flags/)
NATIONS = {
    "France": (1335, "fr"), "England": (1318, "gb-eng"), "Spain": (1362, "es"), "Portugal": (1354, "pt"),
    "Germany": (1337, "de"), "Argentina": (1369, "ar"), "Brazil": (1370, "br"), "Netherlands": (105035, "nl"),
    "Italy": (1343, "it"), "Belgium": (1325, "be"), "Croatia": (1328, "hr"), "Uruguay": (1377, "uy"),
    "Colombia": (111109, "co"), "Morocco": (111111, "ma"), "Denmark": (1331, "dk"), "Switzerland": (1364, "ch"),
    "Norway": (1352, "no"), "Türkiye": (1365, "tr"), "Austria": (1322, "at"), "USA": (1387, "us"),
    "Mexico": (1386, "mx"), "Poland": (1353, "pl"), "Scotland": (1359, "gb-sct"), "Wales": (1367, "gb-wls"),
    "Ireland": (1355, "ie"), "Australia": (1415, "au"), "Canada": (111455, "ca"), "Iceland": (1341, "is"),
    "New Zealand": (111473, "nz"),
}

# FIFA Index name -> name on the star list, where the two sites differ too much to match automatically
STAR_NAMES = {
    "Bayern München": "Bayern", "Tottenham Hotspur": "Tottenham", "West Ham United": "West Ham",
    "Wolverhampton Wanderers": "Wolverhampton", "RC Lens": "Racing Club de Lens", "1. FSV Mainz 05": "Mainz 05",
    "RC Strasbourg": "RC Strasbourg Alsace", "Deportivo Alavés": "Alavés", "Racing Club de Avellaneda": "Racing Club",
    "Union Saint-Gilloise": "Union SG", "Estudiantes de La Plata": "Estudiantes", "Belgrano de Córdoba": "Belgrano",
    "Talleres de Córdoba": "Talleres", "AGF": "Aarhus GF", "Antwerp": "Royal Antwerp", "Los Angeles Galaxy": "LA Galaxy",
    "Norwich City": "Norwich", "San Lorenzo de Almagro": "San Lorenzo", "Gaziantep FK": "Gazişehir Gaziantep FK",
    "SK Rapid": "SK Rapid Wien", "Unión de Santa Fe": "Unión", "Corendon Alanyaspor": "Alanyaspor",
    "FC Rapid": "Rapid București", "Newell's Old Boys": "Newell’s", "AVS Futebol SAD": "AVS", "ESTAC Troyes": "Troyes",
    "Entella": "Virtus Entella", "Red Bull New York": "New York Red Bulls", "Red Star FC (FRANCE)": "Red Star FC",
    "Stade Lavallois MFC": "Laval MFC", "Vålerenga Fotball": "Vålerenga", "FC Argeș": "Argeș Pitești",
    "SSV Ulm 1846": "SSV Ulm", "Sporting KC": "Sporting Kansas City", "Western Sydney Wanderers": "WS Wanderers",
    "Doncaster Rovers": "Doncaster", "Rodez Aveyron Football": "Rodez Averyron", "HamKam Fotball": "Hamarkameratene",
    "Sandefjord Fotball": "Sandefjord", "Strømsgodset Toppfotball": "Strømsgodset", "Tianjin Jinmen Tiger": "Tianjin TEDA",
    "Zhejiang Professional FC": "Zhejiang Pro", "Henan FC": "Henan Jianye", "Drogheda United": "Drogheda",
    "Odense Boldklub": "OB Odense", "RC Deportivo": "Deportivo La Coruña", "Le Havre AC": "Le Havre",
    "Real Valladolid": "Valladolid", "Avellino": "Avelino", "Instituto de Córdoba": "Instituto",
    "Petrolul Ploiești": "FC Petrolul", "Vejle BK": "Vejle Boldklub",
}
# Teams the fuzzy matcher pairs with the wrong club; these always get estimated stars
NO_STAR_MATCH = {"Palmeiras", "Internacional", "Viktoria Plzeň", "Atlético Bucaramanga", "Deportivo La Guaira",
                 "Deportivo Táchira", "Canada"}

LEAGUE_NAMES = {"England Premier League": "Premier League", "Spain Primera División": "LALIGA",
                "Italy Serie A": "Serie A", "Germany 1. Bundesliga": "Bundesliga", "France Ligue 1": "Ligue 1",
                "Holland Eredivisie": "Eredivisie", "-": "Other"}
WOMEN = re.compile(r"Women|WSL|NWSL|Femenina|Frauen")
WOMEN_SLUG = re.compile(r"women|wsl|nwsl|femenina|frauen|liga-f\b", re.I)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url):
    """GET with a disk cache, spacing and retries for FIFA Index's bot challenge."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".html")
    if os.path.exists(path):
        return open(path, encoding="utf8", errors="replace").read()
    for attempt in range(10):
        body = subprocess.run([CURL, "-sL", "--compressed", "-A", UA, url], capture_output=True).stdout.decode("utf8", "replace")
        time.sleep(DELAY)
        if "Just a moment" not in body[:3000]:
            open(path, "w", encoding="utf8").write(body)
            return body
        wait = min(60 * (attempt + 1), 300)  # back off harder each time, up to 5 minutes
        log(f"  rate-limited, waiting {wait}s ({attempt + 1}/10) {url}")
        time.sleep(wait)
    raise SystemExit(f"Still blocked after retries: {url}")


def text_cells(tr):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]


def parse_list(page, suffix):
    """Rows from the all-teams list: id, name, league, OVR/ATK/MID/DEF, plus league page links."""
    rows, leagues = [], set(re.findall(r'href="(/leagues/\d+-[^"/]*%s)"' % re.escape(suffix), page))
    for tr in page.split("<tr")[1:]:
        m = re.search(r'href="/teams/(\d+)-[^"/]*%s"' % re.escape(suffix), tr)
        c = text_cells(tr)
        if m and len(c) >= 6 and c[2].isdigit():
            rows.append(dict(id=m[1], name=c[0], league=c[1], ovr=int(c[2]), att=int(c[3]), mid=int(c[4]), defn=int(c[5])))
    return rows, leagues


def parse_league(page, suffix):
    league = html.unescape(re.search(r"<title>(.*?) – ", page)[1])
    table = page.split("<table")[1] if "<table" in page else ""
    rows = []
    for tr in table.split("<tr")[1:]:
        m = re.search(r'href="/teams/(\d+)-[^"/]*%s"' % re.escape(suffix), tr)
        c = text_cells(tr)
        if m and len(c) >= 5 and c[1].isdigit():
            rows.append(dict(id=m[1], name=c[0], league=league, ovr=int(c[1]), att=int(c[2]), mid=int(c[3]), defn=int(c[4])))
    return rows


def parse_team_page(page, label):
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.S))))
    m = re.search(r"(\d+) OVR (\d+) ATK (\d+) MID (\d+) DEF", t)
    return m and label in t[:400] and dict(ovr=int(m[1]), att=int(m[2]), mid=int(m[3]), defn=int(m[4]))


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(fc|cf|afc|sc|ac|as|ss|ssc|cd|rc|ca|sl|sk|fk|bk|if|club|de|calcio|1899|04|istanbul|balompie|tsg|vfl|vfb)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def star_list(version):
    page = fetch(f"https://www.fifagamenews.com/{version[:2]}-{version[2:]}-team-star-ratings/")
    table = page[page.find("<table"):page.find("</table>")]
    out = []
    for tr in table.split("<tr")[1:]:
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(c) >= 3 and c[0].isdigit() and "(W)" not in c[1]:
            out.append((c[1], float(c[2].replace(",", "."))))
    return out


def match_stars(clubs, stars):
    by_name, by_norm = {}, {}
    for n, s in stars:  # first (highest-ranked) entry wins for duplicate names
        by_name.setdefault(n, s)
        by_norm.setdefault(norm(n), s)
    claimed = {norm(v) for v in STAR_NAMES.values()}
    no_match = {norm(x) for x in NO_STAR_MATCH}
    for r in clubs:
        r["stars"] = None
        if norm(r["name"]) in no_match:
            continue
        if STAR_NAMES.get(r["name"]) in by_name:
            r["stars"] = by_name[STAR_NAMES[r["name"]]]
        elif norm(r["name"]) in by_norm:
            r["stars"] = by_norm[norm(r["name"])]
        else:
            c = difflib.get_close_matches(norm(r["name"]), [k for k in by_norm if k not in claimed], n=1, cutoff=0.75)
            if c:
                r["stars"] = by_norm[c[0]]


def ovr_to_stars(clubs):
    """Most common star rating per OVR among matched clubs, made non-decreasing and gap-filled."""
    counts = collections.defaultdict(collections.Counter)
    for r in clubs:
        if r["stars"] is not None:
            counts[r["ovr"]][r["stars"]] += 1
    table, best = {}, 0.5
    for o in range(40, 100):
        if counts[o]:
            best = max(best, counts[o].most_common(1)[0][0])
        table[o] = best
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="e.g. fc26 or fc27")
    ap.add_argument("--label", help='display name, default "FC 27" style')
    args = ap.parse_args()
    v = args.version.lower()
    label = args.label or f"FC {v[2:]}"

    current = re.search(r"– (FC \d+) Career Mode", fetch("https://fifaindex.com/teams"))
    suffix = "" if current and current[1].replace(" ", "").lower() == v else "/" + v
    base_list = f"https://fifaindex.com/teams{suffix}"
    log(f"Building {label} ({'current game' if not suffix else 'older game'})")

    rows, league_urls, page = {}, set(), 1
    while True:
        found, leagues = parse_list(fetch(f"{base_list}?page={page}"), suffix)
        league_urls |= leagues
        if not found:
            break
        for r in found:
            rows.setdefault(r["id"], r)
        log(f"  list page {page}: {len(rows)} teams so far")
        page += 1
    league_urls = sorted(u for u in league_urls if not WOMEN_SLUG.search(u))
    for i, u in enumerate(league_urls, 1):
        for r in parse_league(fetch("https://fifaindex.com" + u), suffix):
            rows[r["id"]] = r
        log(f"  league {i}/{len(league_urls)}")
    clubs = [r for r in rows.values() if not WOMEN.search(r["league"]) and not r["name"].endswith("W")]

    nations = []
    for i, (name, (tid, flag)) in enumerate(NATIONS.items(), 1):
        d = parse_team_page(fetch(f"https://fifaindex.com/teams/{tid}-x{suffix}"), label)
        if d:
            nations.append(dict(name=name, league="International", flag=flag, **d))
        log(f"  nation {i}/{len(NATIONS)} {name}: {'ok' if d else 'not in this game'}")

    match_stars(clubs, star_list(v))
    est = ovr_to_stars(clubs)
    teams = []
    for r in sorted(clubs, key=lambda r: (-r["ovr"], r["name"])):
        league = LEAGUE_NAMES.get(re.sub(r" \(\d\)$", "", r["league"]), re.sub(r" \(\d\)$", "", r["league"]))
        approx = r["stars"] is None
        teams.append([r["name"], league, r["ovr"], r["att"], r["mid"], r["defn"], est[r["ovr"]] if approx else r["stars"], int(approx), int(r["id"]), None])
    for n in sorted(nations, key=lambda r: (-r["ovr"], r["name"])):
        teams.append([n["name"], "International", n["ovr"], n["att"], n["mid"], n["defn"], est[n["ovr"]], 1, None, n["flag"]])

    data = {"label": label, "built": datetime.date.today().isoformat(), "teams": teams}
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    out = os.path.join(ROOT, "data", f"{v}.js")
    with open(out, "w", encoding="utf8", newline="\n") as f:
        f.write(f"// Generated by tools/build_data.py on {data['built']}. Don't edit by hand; re-run the script.\n")
        f.write("// Rows: [name, league, OVR, ATT, MID, DEF, stars, starsEstimated, fifaIndexId, flagCode]\n")
        f.write(f"(window.FC_DATA = window.FC_DATA || {{}})[{json.dumps(v)}] = ")
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace('"teams":[[', '"teams":[\n[').replace("],[", "],\n["))
        f.write(";\n")
    exact = sum(1 for t in teams if not t[7])
    log(f"Wrote {out}: {len(clubs)} clubs, {len(nations)} national teams, {exact} with listed stars, {len(teams) - exact} estimated")


if __name__ == "__main__":
    main()
