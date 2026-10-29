# FastAPI backend. Build context: backend/
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# the same pinned versions as development (constraints.txt)
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

# tests and scripts too: the image runs the same checks as development (see README, "Testing")
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts

# not root; data/ holds the downloaded airport and city lists (a volume in docker-compose.yml)
RUN useradd --create-home --uid 10001 app && mkdir -p data && chown app:app data
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
