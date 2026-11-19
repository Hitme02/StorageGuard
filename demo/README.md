# StorageGuard control room

A presentation of the project as a dark "control room" console: 20 screens in 7 chapters, built
for a 15-minute talk to evaluators. It runs on the real guard when the backend is up, and falls back
to recorded data when it is not.

## Run it

```sh
make present-build     # once: builds demo/web/dist (needs Node 18+)
make present           # http://127.0.0.1:8765
```

`make present` starts `demo/server.py`. It uses only the Python standard library, so nothing is added
to the research venv. It serves the built frontend and a small API on one port, bound to 127.0.0.1.
Open the page, press `F` for fullscreen and use the arrow keys.

The light at the top right shows which mode the deck is in:

- **Live backend**: every ticket (all 150), every safety method and every guard setting is computed
  on the spot by the real middleware. Real-file runs (screen 4.2) happen in a fresh temporary
  `sg-sandbox-*` folder that is deleted afterwards.
- **Recorded data**: the server is not reachable. The deck uses `demo/web/public/data/`: 18 showcase
  tickets (one or more per trap), the three main models' recorded runs, the full threshold grid, and
  every chart. Tickets that need the backend are greyed out in the control room.

To present without Python, serve `demo/web/dist` with any static server (for example
`cd demo/web && npx vite preview`). The deck then runs in recorded mode.

The backend never calls a language model. Live runs use the scripted careless and correct plans, and
"Real AI" replays recorded episodes from `results/runs/`.

## Presenter keys

| Key | Does |
|---|---|
| `→`, `Space`, `Page Down` / `←`, `Page Up` | next / previous; steps through a screen's reveals first (clicker remotes send Page Down/Up) |
| `Esc` | close the overview or glossary |
| `1` to `7` | jump to a chapter |
| `C` | core path on/off: skips the 5 detour screens (on by default) |
| `O` | overview of all screens |
| `G` | glossary: every term introduced so far, in plain words |
| `E` | engineer view: file paths, formulas and API calls under each screen |
| `H` | high contrast, for washed-out projectors |
| `F` | fullscreen |

Click the timer to reset it. Each screen has its own URL (`#/3.2/4` is screen 3.2, reveal 4), so a
browser bookmark can take you back to any point.

## 15-minute plan (core path)

The full script, with what to say at every reveal, the demo steps and likely questions, is in
[`SPEAKER_NOTES.md`](SPEAKER_NOTES.md).

| Chapter | Screens | Time |
|---|---|---|
| 1. The problem | 1.1 one ticket, 1.2 incidents, 1.3 storage in 60 s, 1.5 hidden links, 1.6 why current methods miss it | 4:00 |
| 2. The benchmark | 2.1 the pretend company, 2.2 150 tickets and 12 traps | 1:30 |
| 3. How it works | 3.1 try it on a copy, 3.2 the six-step pipeline, 3.3 four answers | 3:00 |
| 4. Try it | 4.1 control room (live) | 2:00 |
| 5. Results | 5.1 out of 100 tickets, 5.2 against the alternatives | 2:15 |
| 6. Reality check | 6.1 real files and OpenZFS | 1:15 |
| 7. Wrap-up | 7 three things to remember | 1:00 |

Detours for questions: 1.4 meet the AI admin, 2.3 how harm is judged, 4.2 the same plan on real
files, 5.3 deep dives (ablations, model size, planted instructions, explanations, speed, trap by
trap), 6.2 what running it for real taught us.

## Refresh the recorded data

After new results (`sg report`), regenerate the fallback data. This takes about 3 minutes and runs
the real-file sandbox for the showcase tickets:

```sh
make present-data                         # everything
venv/bin/python demo/export.py --results-only   # only results.json and tasks.json (seconds)
```

Every number in the deck comes from `results/REPORT.md`, `results/aggregates.csv` or a run. None
is typed into the frontend.

## Develop

```sh
make present                  # backend on :8765
cd demo/web && npm run dev    # Vite on :5173, proxies /api to :8765
npm run shots -- <out-dir>    # screenshot every screen at 1600x900 (uses the installed Chrome)
```

| Path | What |
|---|---|
| `demo/engine.py` | backend logic: task list, live runs through the middleware, replay, maps, threshold sweep |
| `demo/server.py` | standard-library HTTP server: `/api/*` plus the built frontend |
| `demo/export.py` | writes `demo/web/public/data/` |
| `demo/web/src/slides/` | one file per chapter |
| `demo/web/src/components/` | storage map, step-by-step run view, charts, shared UI |
| `demo/web/src/content/` | glossary and the trap and incident texts |
