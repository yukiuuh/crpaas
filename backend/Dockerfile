FROM python:3.14.0-slim-trixie AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# --- Test Stage ---
FROM base AS test
ENV PYTHONPATH=/app
RUN pytest

# --- Production Stage ---
FROM base AS final
EXPOSE 8080
CMD ["/bin/sh", "-c", "python /app/db_init.py && uvicorn main:app --host 0.0.0.0 --port 8080"]