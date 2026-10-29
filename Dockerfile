# Canonical image is built from ./measure (see docker-compose.yml).
# This file builds the same image when the context is the repository root.
FROM python:3.12-slim

WORKDIR /app
COPY measure/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY measure /app/measure

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DATA_DIR=/data

ENTRYPOINT ["python", "-m", "measure"]
CMD ["run"]
