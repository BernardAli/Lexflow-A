FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng poppler-utils postgresql-client fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
COPY deployment/requirements.txt /app/deployment/requirements.txt
RUN pip install --no-cache-dir -r /app/deployment/requirements.txt && useradd --uid 10001 --create-home lexflow
COPY . /app
RUN mkdir -p /data/vault /data/exports /app/staticfiles && chown -R lexflow:lexflow /data /app/staticfiles
USER lexflow
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-"]
