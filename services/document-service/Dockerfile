FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY --from=libs common_auth /libs/common_auth
RUN pip install --no-cache-dir /libs/common_auth
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY requirements-vietocr.txt .
RUN pip install --no-cache-dir torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements-vietocr.txt && pip install --no-cache-dir --no-deps vietocr==0.3.13
COPY scripts/download_vietocr_model.py /app/scripts/download_vietocr_model.py
RUN python /app/scripts/download_vietocr_model.py
COPY app ./app
COPY scripts/check_local_ocr*.py /app/scripts/
COPY scripts/setup_identity_profile.py scripts/setup_document_outbox.py scripts/check_release.py /app/scripts/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
