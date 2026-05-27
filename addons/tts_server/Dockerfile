ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Azure Speech SDK requires libssl + libasound; the HA python base image
# ships Alpine, so install the runtime deps via apk before pip-installing
# the wheels.
RUN apk add --no-cache \
    libstdc++ \
    libffi \
    openssl \
    ca-certificates \
    alsa-lib

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY tts_server.py /app/tts_server.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
