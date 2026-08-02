# GLOF Portal

Glacial Lake Outburst Flood (GLOF) monitoring portal for Gilgit-Baltistan — FastAPI backend + React (Vite) frontend with satellite risk intelligence, lake inventory, population exposure, and historical analysis.

## Project structure

```
├── backend/          # FastAPI API (port 8000)
├── frontend/         # React + Vite UI (port 5173)
├── docker-compose.yml
└── render.yaml       # Free-tier Render blueprint
```

## Local development

### Backend

```bash
cd backend
python -m venv ../.venv
# Windows: ..\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API: http://127.0.0.1:8000 · Docs: http://127.0.0.1:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://127.0.0.1:5173

Optional: create `frontend/.env` with:

```
VITE_API_URL=http://127.0.0.1:8000
```

## Free hosting (recommended)

| Layer | Service | Plan |
|-------|---------|------|
| API | [Render](https://render.com) Web Service | Free |
| UI | [Vercel](https://vercel.com) or Render Static | Free |

### 1. Backend on Render

1. Push this repo to GitHub.
2. [New Web Service](https://dashboard.render.com/select-repo?type=web) → select repo.
3. Settings:
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Environment variables:
   - `SEED_ON_START` = `1`
   - `DATABASE_URL` = `sqlite:////tmp/glof.db`
   - `CORS_ORIGINS` = your frontend URL (e.g. `https://your-app.vercel.app`)
5. Deploy → note the API URL (`https://….onrender.com`).

> Free Render instances **spin down** after ~15 minutes idle. First request may take 30–60s.

### 2. Frontend on Vercel

```bash
cd frontend
npx vercel --prod
```

Or import the GitHub repo in Vercel:

- **Root Directory:** `frontend`
- **Build Command:** `npm run build`
- **Output:** `dist`
- Env: `VITE_API_URL` = `https://your-api.onrender.com`

### Blueprint deploy

`render.yaml` defines both services. Import as a Blueprint and update `VITE_API_URL` / `CORS_ORIGINS` after first deploy.

## Features

- Lakes inventory (register, rename, bulk, risk scoring)
- Interactive risk map (Leaflet)
- Population exposure zones
- GEE historical area series + NDWI / RGB thumbnails
- Satellite detection endpoint
- Dark / light theme + motion UI

## License

Internship / educational project — NCGSA 2026.
