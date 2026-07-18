# Overcut — web UI

Electron + React + Vite + Tailwind display layer. Reads the same
`data/race.db` SQLite file the Python engine writes; no Python required
in-process.

## Setup

```bash
cd ui
npm install
npm run rebuild        # compiles better-sqlite3 for Electron's ABI (one-time)
```

## Run

**Browser mode (mock data)** — UI development without any race data:

```bash
npm run browser        # Vite dev server at http://localhost:5173
```

**Electron (live data)** — needs two things running:

1. The net-analysis writer (computes net positions from raw standings):

   ```bash
   ../venv/bin/python ../src/poller_daemon.py            # or --series wec / --oid <oid>
   ```

   (Opening the PyQt6 dashboard also works — its Poller writes the same table.)

2. The app:

   ```bash
   npm run dev            # starts Vite + Electron together
   ```

Without the writer, the board still shows raw standings — the NET column
and gap breakdown just stay empty.

## Layout

- `electron/main.cjs` — main process: polls `race.db` every 2 s (readonly),
  ships payloads to the renderer over IPC
- `electron/preload.cjs` — exposes `window.racenet.{onRows,offRows}`
- `src/` — React renderer; falls back to `mock.ts` data when
  `window.racenet` is absent (i.e. plain browser)
