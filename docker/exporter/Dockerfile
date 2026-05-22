FROM python:3.12-slim

# Install LibreOffice + poppler for thumbnail generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# html-assets are mounted as a volume via docker-compose (./html-assets:/app/html-assets:ro)
# No COPY needed here — the volume mount handles it at runtime.

EXPOSE 8004
ENV HTML_ASSETS_DIR=/app/html-assets
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004"]
