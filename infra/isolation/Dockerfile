ARG BASE_IMAGE=python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir playwright==1.63.0 httpx==0.28.1 \
    && PLAYWRIGHT_BROWSERS_PATH=/ms-playwright playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin collector
ENV HOME=/tmp PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY verify.py /app/verify.py
USER 10001:10001
CMD ["sleep", "infinity"]
