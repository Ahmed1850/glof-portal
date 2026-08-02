# GLOF Portal

Glacial Lake Outburst Flood (GLOF) monitoring portal for Gilgit-Baltistan — FastAPI backend + React (Vite) frontend with satellite risk intelligence, lake inventory, population exposure, and historical analysis.

## Live links

| Resource | URL |
|----------|-----|
| **Live app** | https://glof-portal.onrender.com |
| **API health** | https://glof-portal.onrender.com/health |
| **API root** | https://glof-portal.onrender.com/api |
| **API docs (Swagger)** | https://glof-portal.onrender.com/docs |
| **Lakes API** | https://glof-portal.onrender.com/lakes/ |
| **GitHub repository** | https://github.com/Ahmed1850/glof-portal |

> Free Render hosting may sleep after ~15 minutes of idle time. The first request after sleep can take 30–60 seconds.

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

## Free hosting (Render — recommended)

One free web service serves **both** the API and the React UI.

### Deploy in ~5 minutes

1. Open: [Render → New Blueprint](https://dashboard.render.com/select-repo?type=blueprint)  
   (or **New → Web Service** and connect this GitHub repo)
2. Connect GitHub → select **`Ahmed1850/glof-portal`**
3. Use the included `render.yaml` (or set manually):
   - **Build command:**
     ```
     pip install -r backend/requirements.txt
     cd frontend && npm install && VITE_API_URL= npm run build
     mkdir -p ../backend/static && cp -r dist/* ../backend/static/
     ```
   - **Start command:** `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Env vars:**
     - `SEED_ON_START` = `1`
     - `DATABASE_URL` = `sqlite:////tmp/glof.db`
     - `PYTHON_VERSION` = `3.12.8`
4. Create Web Service → wait for deploy → open `https://glof-portal-xxxx.onrender.com`

> Free instances **sleep after ~15 min idle**. First load after sleep can take 30–60 seconds.

### Optional: split UI on Vercel

- **API** still on Render (`rootDir: backend`)
- **UI** on Vercel with root `frontend` and env `VITE_API_URL=https://your-api.onrender.com`

## Features

- Lakes inventory (register, rename, bulk, risk scoring)
- Interactive risk map (Leaflet)
- Population exposure zones
- GEE historical area series + NDWI / RGB thumbnails
- Satellite detection endpoint
- Dark / light theme + motion UI

## License

Internship / educational project — NCGSA 2026.
