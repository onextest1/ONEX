# ONEX panel - production image (Railway / any Docker host)
# Xray-core (SideRail VMess-WS + VLESS-XHTTP) and sing-box (native protocols)
# are baked in at build time, so nothing has to be downloaded at runtime.
FROM python:3.12-slim

ARG XRAY_VERSION=v26.9.9
ARG SINGBOX_VERSION=1.14.1
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/data \
    ONEX_XRAY_BIN=/usr/local/bin/xray \
    ONEX_SINGBOX_BIN=/usr/local/bin/sing-box \
    XRAY_LOCATION_ASSET=/usr/local/share/xray

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl unzip; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64) xa=Xray-linux-64.zip; sa=amd64 ;; \
      arm64) xa=Xray-linux-arm64-v8a.zip; sa=arm64 ;; \
      *) echo "unsupported arch $arch"; exit 1 ;; \
    esac; \
    mkdir -p /usr/local/share/xray /tmp/x; \
    curl -fsSL -o /tmp/x/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${xa}" \
      || curl -fsSL -o /tmp/x/xray.zip "https://github.com/XTLS/Xray-core/releases/latest/download/${xa}"; \
    unzip -o /tmp/x/xray.zip -d /tmp/x; \
    install -m 0755 /tmp/x/xray /usr/local/bin/xray; \
    cp /tmp/x/geoip.dat /tmp/x/geosite.dat /usr/local/share/xray/ 2>/dev/null || true; \
    ( curl -fsSL -o /tmp/x/sb.tgz "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-linux-${sa}.tar.gz" \
      && tar -xzf /tmp/x/sb.tgz -C /tmp/x \
      && install -m 0755 "$(find /tmp/x -type f -name sing-box | head -n1)" /usr/local/bin/sing-box ) \
      || echo "WARN: sing-box not baked in, ONEX will fetch it at runtime"; \
    rm -rf /tmp/x /var/lib/apt/lists/*; \
    xray version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "main.py"]
