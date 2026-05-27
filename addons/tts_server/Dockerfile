FROM python:3.11-slim

ENV LANG=C.UTF-8

# Azure Speech SDK ships glibc (manylinux) wheels only — no musl/Alpine
# build exists — so this image is Debian-based (python:3.11-slim). The
# SDK needs libssl and libasound2 at runtime; install them via apt.
# libssl3 is the Debian 12 (Bookworm) package name, which is what
# python:3.11-slim currently resolves to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libssl3 \
        libasound2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY tts_server.py /app/tts_server.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
