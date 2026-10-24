# FC Team Picker

Randomly assigns EA SPORTS FC teams to the people playing, with a spin-the-wheel tab and a
tournament tab (league or knockout). Live at **https://fifapicker.pages.dev/**.

It's a static site: open `index.html` directly or host the folder anywhere.

## Files

| Path | What it is |
|---|---|
| `index.html` | The whole app (layout, styles, script) |
| `data/fc26.js`, `data/fc27.js` | Team data per game, generated, don't edit by hand |
| `flags/` | National team flags |
| `tools/build_data.py` | Rebuilds a data file from the sources below |

## Updating the team data

```
python tools/build_data.py fc27
```

This writes `data/fc27.js`. For a new game (e.g. FC 28), run it with `fc28`, then add
`<script src="data/fc28.js" onerror="this.remove()"></script>` next to the other data
scripts in `index.html`; the version switch picks it up automatically.

* **OVR / ATT / MID / DEF**: [FIFA Index](https://fifaindex.com) (club list, league pages, national team pages)
* **Star ratings**: [fifagamenews.com](https://www.fifagamenews.com/fc-26-team-star-ratings/)
* Teams missing from the star list (national teams, most South American / Rest of World clubs)
  get the star rating most common among listed teams with the same OVR, shown with **≈**.

Fetched pages are cached in `tools/.cache/` (git-ignored), so re-running is quick and an
interrupted build resumes where it stopped. FIFA Index rate-limits, so a fresh build can take
a while. Needs the curl that comes with Git for Windows.

Crests load from FIFA Index's image server at runtime, falling back to Wikipedia, then initials.
