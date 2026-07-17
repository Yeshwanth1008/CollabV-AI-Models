# CollabV AI Frontend

Production Next.js 14 dashboard for CollabV AI.

## Setup

```bash
cp .env.local.example .env.local
# Edit NEXT_PUBLIC_API_BASE if your backend is not at localhost:8000

npm install
npm run dev
```

Open http://localhost:3000.

## Build for production

```bash
npm run build
npm run start
```

The `next.config.js` ships with `output: "standalone"` for Docker.

## Pages

- `/` — Landing page with stats and value props
- `/match` — Submit an R&D need, view ranked matches with explanations, deal probability, and feedback buttons
- `/professors` — Searchable directory; click through to per-professor profile
- `/professors/[id]` — Full profile with patent portfolio scoring and readiness breakdown
- `/analytics` — Department readiness heatmap, feedback summary, retraining controls
- `/contracts` — Generate MoUs from 5 templates; parse pasted MoU text

## Notes

The frontend has no auth wired up by default. If you deploy publicly, add NextAuth.js
or your preferred auth on top of the App Router middleware before exposing the admin
controls (`/analytics` retrain button, `/embeddings/rebuild`, etc).
