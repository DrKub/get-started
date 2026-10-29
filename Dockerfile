FROM python:3.12-slim

WORKDIR /app
COPY measure/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY measure /app/measure
COPY data /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OUTPUT_DIR=/app/data/raw

ENTRYPOINT ["python", "-m", "measure"]
CMD ["run"]
