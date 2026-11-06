# Builds the dashboard, then runs the API and serves the dashboard from one server.
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx vite build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=web /web/dist ./web_dist
ENV WEB_DIST=/app/web_dist \
    DATABASE_PATH=/tmp/receptionist.db \
    STT_PROVIDER=mock LLM_PROVIDER=mock TTS_PROVIDER=mock
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}