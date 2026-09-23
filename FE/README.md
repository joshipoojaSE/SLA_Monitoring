# Frontend (React)

Upload screen and single-page SLA dashboard for the SLA Monitoring API.

Vite + React 19 + TypeScript, TanStack Query for fetching and caching, React Router, Tailwind CSS.

## Setup

```bash
cd FE
npm install
cp .env.example .env   # set VITE_API_URL to the API's base URL
npm run dev            # http://localhost:5174
```

The API must allow this origin: set `CORS_ORIGINS` in `BE/.env` (it defaults to `http://localhost:5174`, the port `vite.config.ts` pins).

`npm run build` type-checks and writes a static site to `dist/`.

## Screens

| Route | What it does |
|---|---|
| `/upload` | Sends a CSV to `POST /uploads`, then `POST /outage` for the new file, and shows the cleaning report |
| `/` | Dashboard for one uploaded file (newest by default): collapsible SLA stats on top, filterable check logs below |

The dashboard keeps its state in the query string (`file`, `mode`, `from`, `to`, `service`, `outcome`, `page`), so a filtered view can be reloaded or shared as a link. Clicking a service row filters the logs to that service; "View logs" on an incident filters them to its service and day.

All times are UTC, the grid the checks sit on and the days the API filters by.

## Layout

```
src/
  api/          client.ts (fetch wrapper), types.ts (mirrors the API's response models)
  hooks/        queries.ts (TanStack Query hooks), useDashboardParams.ts (URL state)
  lib/          format.ts (UTC dates, percentages, durations)
  components/   ui.tsx, stats/ (KPI tiles, service table, incidents), logs/ (filters, table, pagination)
  pages/        Dashboard.tsx, UploadPage.tsx
```
