# gcloud run deploy --source . uses this when it sits at the ROOT of the source
# directory. Anywhere else and gcloud silently falls back to Buildpacks, which
# cannot find an entrypoint here: there is no Procfile and no root main.py.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY extractor/ ./extractor/

# Cloud Run supplies PORT. One worker: the work is IO-bound on the model call,
# and concurrency is the platform's job, not the process's.
ENV PORT=8080
CMD exec uvicorn extractor.main:app --host 0.0.0.0 --port ${PORT} --workers 1
