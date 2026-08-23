# Anaplan Workspace Capacity Forecaster — Railway deployment package

This app is fully client-side (all parsing/calculation runs in the browser's
JavaScript). `server.js` is a ~40-line, zero-dependency Node server that just
serves `public/index.html` — that's all Railway needs to host it.

## Folder structure
```
.
├── public/
│   └── index.html      <- the app itself
├── server.js            <- static file server (no npm deps required)
├── package.json          <- tells Railway how to run it (Nixpacks auto-detects Node)
├── railway.json           <- explicit build/start config (optional but recommended)
└── Procfile                <- fallback start command (optional)
```

## Option A — Deploy from GitHub (recommended)
1. Create a new GitHub repo and push this folder to it:
   ```bash
   git init
   git add .
   git commit -m "Anaplan workspace forecaster"
   git branch -M main
   git remote add origin https://github.com/<you>/anaplan-forecaster.git
   git push -u origin main
   ```
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo** → pick the repo.
3. Railway auto-detects Node via Nixpacks, runs `npm install` (no-op, zero deps) then `npm start`.
4. Once it deploys, click **Settings → Networking → Generate Domain** to get a public URL.

## Option B — Deploy with the Railway CLI (no GitHub needed)
1. Install the CLI: `npm install -g @railway/cli`
2. From inside this folder:
   ```bash
   railway login
   railway init
   railway up
   ```
3. Then generate a public domain:
   ```bash
   railway domain
   ```

## Verifying locally before you deploy
```bash
npm start
# or: node server.js
```
Then open http://localhost:3000 (Railway sets `PORT` automatically in production;
locally it defaults to 3000).

## Notes
- No environment variables are required — the app has no backend calls, no
  database, and stores nothing server-side. Everything (paste parsing, the
  cell-count math, the projected-vs-current comparison) runs in the visitor's
  browser.
- The Python script (`anaplan_workspace_forecaster.py`, not included in this
  bundle) mirrors the same calculation logic for offline/CLI use, but the
  deployed web app does **not** call it — the two are kept in sync manually
  as reference implementations of the same model. If you'd rather have the
  browser call a real Python backend instead of duplicating the logic in JS,
  say the word and I can wrap it in a small Flask/FastAPI service and adjust
  this package to run both processes on Railway.
