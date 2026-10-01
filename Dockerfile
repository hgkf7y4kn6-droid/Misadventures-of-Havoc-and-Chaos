# The Misadventures of Havoc and Chaos — single image: built web client served by the game server.
FROM node:22-alpine AS web
WORKDIR /repo
# npm workspaces: install only the web client and the shared packages it uses.
COPY package.json package-lock.json ./
COPY packages/ packages/
COPY frontend/package.json frontend/
COPY workers/edge/package.json workers/edge/
COPY apps/mobile/package.json apps/mobile/
RUN npm ci --workspace @havoc/web --include-workspace-root=false
COPY frontend/ frontend/
RUN npm run build -w @havoc/web

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /repo/frontend/dist /app/frontend/dist
RUN useradd --create-home havoc && mkdir -p /data/audio && chown -R havoc /data
USER havoc
ENV HAVOC_TTS_CACHE_DIR=/data/audio
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
