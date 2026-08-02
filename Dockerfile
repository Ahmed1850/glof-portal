# Optional Docker deploy (Render / any container host)
FROM node:20-bookworm AS frontend
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
# Same-origin API when served from FastAPI
ENV VITE_API_URL=
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/ /app/backend/
COPY --from=frontend /frontend/dist/ /app/backend/static/
WORKDIR /app/backend
ENV DATABASE_URL=sqlite:////tmp/glof.db SEED_ON_START=1 CORS_ORIGINS=*
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
