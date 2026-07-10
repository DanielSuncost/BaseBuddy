# BaseBuddy - AI-Powered Security Camera System
# Application lives in basebuddy/; runtime data stays at repo root.

FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-core.txt requirements-ml.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-core.txt

COPY main.py ./
COPY basebuddy/ basebuddy/
COPY env.example ./

ENV BASEBUDDY_REPO_ROOT=/app
ENV BASEBUDDY_APP_ROOT=/app/basebuddy
ENV HOST=0.0.0.0
ENV PORT=5000
ENV FLASK_ENV=production
ENV DETECTION_ENABLED=false

EXPOSE 5000

CMD ["python", "main.py"]
