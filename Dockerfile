# One container: builds the React dashboard, then runs the FastAPI service that serves it.
# Used by Render (render.yaml) and works on any Docker host. Fits a 512 MB instance (~250 MB idle, ~380 MB peak).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib MALLOC_ARENA_MAX=2
RUN useradd -m -u 1000 app
WORKDIR /app
COPY deploy/requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY --chown=app backend ./backend
COPY --chown=app --from=web /web/dist ./frontend/dist
RUN mkdir -p data/uploads && chown -R app /app
USER app
EXPOSE 10000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips '*'"]
