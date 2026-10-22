FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-liberation poppler-utils && rm -rf /var/lib/apt/lists/*
COPY --from=libs common_auth /libs/common_auth
RUN pip install --no-cache-dir /libs/common_auth
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates/reference-catalog ./templates/reference-catalog
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

