FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY locales/ /build/locales/
RUN npm run build

FROM python:3.11-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/
WORKDIR /app/backend
ENV PYTHONUTF8=1 PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-editable --no-install-project
COPY backend/ ./
COPY locales/ /app/locales/
RUN uv sync --frozen --no-editable
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist
ENV POF_SERVE_FRONTEND=1 FLASK_HOST=0.0.0.0 FLASK_PORT=5001 MIROFISH_RESEARCH_ONLY=1
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["/app/backend/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=3)"]
CMD ["/app/backend/.venv/bin/python", "-X", "utf8", "run.py"]
