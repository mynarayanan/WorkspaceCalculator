# Anaplan Workspace Capacity Forecaster — Vercel package

A fully client-side static app: all parsing and cell-count math runs in the
browser. Vercel just serves `public/index.html` (no build step, no server).

## Structure
```
public/index.html                 <- the app (Vercel output directory)
vercel.json                       <- static config (no build, outputDirectory=public)
package.json                      <- minimal metadata
anaplan_workspace_forecaster.py   <- standalone reference/CLI version (not deployed)
sample_data/                      <- lists.csv and modules.csv to test the Upload CSV buttons
```

## Deploy option A: Vercel CLI (fastest)
```bash
npm i -g vercel
cd vercel_package
vercel login
vercel            # preview deployment
vercel --prod     # production deployment
```
When prompted: "Link to existing project?" -> No; accept defaults
(no framework, output directory `public` is read from vercel.json).

## Deploy option B: GitHub + Vercel dashboard
1. Push this folder to a GitHub repo.
2. vercel.com -> Add New -> Project -> Import the repo.
3. Framework Preset: **Other**. Leave Build Command and Install Command empty.
   Output Directory: `public`. Click Deploy.

## Local preview
```bash
npx serve public
```

## Notes
- No environment variables needed.
- The Python file is not executed on Vercel; it mirrors the same model for
  offline/CLI use. To expose it as an API, it can be wrapped as a Vercel
  Python serverless function under `api/`.
