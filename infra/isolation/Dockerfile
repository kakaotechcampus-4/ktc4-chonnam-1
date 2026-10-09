ARG BASE_IMAGE=python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir playwright==1.63.0 httpx==0.28.1 starlette==1.7.0 uvicorn==0.54.0 \
    && PLAYWRIGHT_BROWSERS_PATH=/ms-playwright playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin collector
ENV HOME=/tmp PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY verify.py /app/verify.py
# prepare.sh 가 저장소의 scanner/fetch.py·models.py 를 이 폴더로 복사해 둔다.
COPY scanner/ /app/scanner/
COPY collector/ /app/collector/
USER 10001:10001
EXPOSE 8443
CMD ["python", "-m", "uvicorn", "--factory", "collector.app:create_app_from_env", \
     "--host", "0.0.0.0", "--port", "8443", "--workers", "1", \
     "--ssl-certfile", "/run/secrets/collector_cert", "--ssl-keyfile", "/run/secrets/collector_key", \
     "--no-server-header", "--no-access-log", "--limit-concurrency", "8", "--timeout-keep-alive", "5"]
