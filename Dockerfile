# Minimal backend image for GramVault. This is scaffolding for A2-A6 to
# extend (e.g. add faster-whisper model downloads, frontend build stage).
FROM python:3.11-slim

# ffmpeg is required for video keyframe extraction (see config.yaml: video.*).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY config.yaml config.example.yaml ./

RUN pip install --no-cache-dir -e .

# TODO(A5): once frontend/ has a real Vite build, add a build stage here and
# copy frontend/dist into the image so gramvault.main can serve it in prod.

EXPOSE 8000

CMD ["gramvault", "serve"]
