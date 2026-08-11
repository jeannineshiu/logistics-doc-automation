FROM python:3.12-slim

# Fail fast and log unbuffered — without this, uvicorn output sits in the pipe
# buffer and `docker compose logs` looks frozen during an incident.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Drop root: nothing here needs to write outside /app, and the container is
# reachable from n8n and the host.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
