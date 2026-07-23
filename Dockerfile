FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PALSITTER_DATA_DIR=/var/lib/palsitter \
    PALSITTER_CONFIG_DIR=/var/lib/palsitter/config \
    PALSITTER_PROFILE_DIR=/var/lib/palsitter/profile \
    PALSITTER_LOG_DIR=/var/lib/palsitter/logs \
    PALSITTER_HOST=127.0.0.1 \
    PALSITTER_PORT=22368

WORKDIR /opt/palsitter

COPY requirements-runtime.txt script/linux/install-dependencies.sh /tmp/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && chmod +x /tmp/install-dependencies.sh \
    && /tmp/install-dependencies.sh \
    && python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --requirement /tmp/requirements-runtime.txt \
    && rm -rf /var/lib/apt/lists/* /tmp/install-dependencies.sh /tmp/requirements-runtime.txt

COPY gui.py ./
COPY module ./module
COPY assets ./assets

RUN groupadd --gid 1000 palsitter \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin palsitter \
    && mkdir -p /var/lib/palsitter/config /var/lib/palsitter/profile /var/lib/palsitter/logs \
    && chown -R palsitter:palsitter /var/lib/palsitter

USER palsitter

EXPOSE 22368/tcp
STOPSIGNAL SIGINT
ENTRYPOINT ["python", "gui.py"]
