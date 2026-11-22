# StorageGuard presentation

A minimal deck for a 15-minute talk to evaluators: a landing screen, then 13 screens in 6 chapters. Each screen has one short
headline, one animated picture and at most one big number, and the button at the bottom right says what
happens next. It runs on the real guard when the backend is up, and falls back to recorded data when it
is not.

The earlier 20-screen "control room" deck is still there: open the same address with `?classic`
(for example `http://127.0.0.1:8765/?classic`). Its keys and notes are unchanged.

## Run it

```sh
make present-build     # once: builds demo/web/dist (needs Node 18+)
make present           # http://127.0.0.1:8765
```

`make present` starts `demo/server.py`. It uses only the Python standard library, so nothing is added
to the research venv. It serves the built frontend and a small API on one port, bound to 127.0.0.1.
Open the page, press `F` for fullscreen and use the arrow keys.

The light at the top right shows which mode the deck is in:

- **Live**: the playground ("Try it") runs every ticket and every guard setting through the real
  middleware on the spot.
- **Recorded**: the server is not reachable. The deck uses `demo/web/public/data/`. Every screen works;
  in the playground the eight listed tickets, all safety methods and the three main models replay from
  the recordings, and changing the guard settings needs the backend.

To present without Python, serve `demo/web/dist` with any static server. The deck then runs in
recorded mode.

The backend never calls a language model. Live runs use the scripted careless and careful plans, and
"Real AI" replays recorded episodes from `results/runs/`.

## Presenter keys

| Key | Does |
|---|---|
| `→`, `Space`, `Page Down` | next: steps through a screen's reveals first, then moves on (the button names the next step) |
| `←`, `Page Up` | back |
| `1` to `6` | jump to a chapter |
| `0`, `Home` | back to the landing screen |
| `F` | fullscreen |

Each screen has its own URL (`#/checks/2` is the third reveal of "Today's checks"), so a bookmark can
take you back to any point.

## The screens

| Chapter | Screen (URL) | Reveals | What it shows |
|---|---|---|---|
| | `start` | 1 | landing: the logo animates in, with the one-line pitch and the team |
| Problem | `ticket` | 4 | the ticket; the agent deletes three "idle" volumes; one was the live copy of a tier-1 database; 40–53% |
| | `linked` | 5 | storage in one picture (volume, snapshots, clone, replica, backup), then the blast radius |
| | `checks` | 4 | a spotlight shows how far prompt rules and an AI checker can see, then everything |
| Idea | `copy` | 3 | the guard sits between agent and storage, rehearses the call on a copy, answers with a reason |
| | `decide` | 3 | six checks, then the risk scale with the four answers from real decisions |
| Test bed | `tickets` | 2 | 150 tickets sort into routine, unclear, 65 traps and 15 planted instructions |
| Try it | `play` | 1 | the playground: ticket, who works it, safety method, guard settings |
| Results | `hundred` | 3 | 100 dots: harm and finished tickets, no guard against StorageGuard, per model |
| | `versus` | 3 | harm against human asks for all five methods |
| | `scale` | 2 | 7B to 72B: no guard stays at 41–51%, StorageGuard at 0–1% |
| Reality check | `zfs` | 2 | one ticket replayed on OpenZFS: pick the AI and the ticket, two safety methods side by side; the storage before, every `zfs` command, what ZFS destroyed, and what was lost |
| | `real` | 3 | 866 of 866 on real files, 95.7% on OpenZFS, 0 TB against 104.9 TB destroyed |
| | `end` | 4 | three things to remember, then questions |

## Refresh the recorded data

After new results (`sg report`), regenerate the fallback data. This takes about 3 minutes and runs
the real-file sandbox for the showcase tickets:

```sh
make present-data                         # everything
venv/bin/python demo/export.py --results-only   # only results.json and tasks.json (seconds)
venv/bin/python demo/export_zfs.py              # only the OpenZFS screen (seconds)
```

Every number in the deck comes from `results/REPORT.md`, `results/aggregates.csv` or a run. The one
exception is the 866-of-866 agreement count, copied from `results/physical/REPORT.md` into
`web/src/deck/screens/reality.tsx`.

## Develop

```sh
make present                  # backend on :8765
cd demo/web && npm run dev    # Vite on :5173, proxies /api to :8765
npm run deck-shots -- <out-dir> http://localhost:5173   # screenshot every screen and reveal at 1600x900 (installed Chrome)
npm run shots -- <out-dir>    # the same for the classic deck
```

| Path | What |
|---|---|
| `demo/engine.py` | backend logic: task list, live runs through the middleware, replay, maps, threshold sweep |
| `demo/server.py` | standard-library HTTP server: `/api/*` plus the built frontend |
| `demo/export.py` | writes `demo/web/public/data/` |
| `demo/export_zfs.py` | writes `demo/web/public/data/zfs/` for the OpenZFS screen, from the recorded replay in `results/zfs` (no VM needed); `export.py` runs it too |
| `demo/web/src/deck/` | the minimal deck: `Deck.tsx` (shell and screen list), `Graph.tsx` (the animated storage map), `screens/` (one file per chapter) |
| `demo/web/src/slides/`, `components/`, `content/`, `app/` | the classic deck (`?classic`) |
| `demo/web/src/lib/` | data layer and plain-English wording, shared by both decks |
