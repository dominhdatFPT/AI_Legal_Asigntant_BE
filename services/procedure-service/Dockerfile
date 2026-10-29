# Multi-stage or Monorepo Dockerfile for procedure-service
# Lưu ý khi build: Đứng từ thư mục gốc BE để docker build context có thể truy cập /libs/common_auth
# Ví dụ: docker build -f services/procedure-service/Dockerfile -t procedure-service .

FROM python:3.11-slim

# Tạo non-root user appuser để tăng tính an ninh (L8)
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

# Copy thư viện xác thực chung và cài đặt
COPY --from=libs common_auth /libs/common_auth
RUN pip install --no-cache-dir /libs/common_auth

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY database ./database
COPY ["Template các thủ tục kinh doanh", "./Template các thủ tục kinh doanh"]
COPY ["Template các thủ tục đất đai", "./Template các thủ tục đất đai"]
RUN chown -R appuser:appuser /app

USER appuser

ENV PROCEDURE_SERVICE_PORT=8000
EXPOSE ${PROCEDURE_SERVICE_PORT}

# Healthcheck endpoint (L8)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, os; port = os.getenv('PROCEDURE_SERVICE_PORT', '8000'); urllib.request.urlopen(f'http://localhost:{port}/api/v1/procedures/health')" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PROCEDURE_SERVICE_PORT:-8000}"]
