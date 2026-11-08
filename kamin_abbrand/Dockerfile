# Festes Abbild für aarch64 und amd64. Ohne BUILD_FROM: Der Supervisor reicht es
# seit 2026.04 nicht mehr durch, eine build.yaml ist nicht mehr nötig.
FROM python:3.14.10-slim

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.type="app"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app
COPY integration /app/integration
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
