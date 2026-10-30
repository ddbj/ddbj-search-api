ARG VERSION=0.0.0.dev0

# builder: uses the full image because uv needs git to fetch ddbj-search-converter.
FROM python:3.14-trixie AS builder

ARG VERSION

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --no-dev --no-install-project -P ddbj-search-converter

COPY . .

RUN SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} uv sync --no-dev -P ddbj-search-converter

# dev: adds the dev dependency group, uv, and debugging tools on top of builder.
FROM builder AS dev

ARG VERSION

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    iputils-ping \
    jq \
    less \
    procps \
    tree \
    vim-tiny && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} uv sync -P ddbj-search-converter

ENV HOME=/home/app
RUN mkdir -p /home/app && chmod 777 /home/app

ENTRYPOINT []
CMD ["sleep", "infinity"]

# runtime: the default target. Contains only the Python runtime, the venv, and the app.
FROM python:3.14-slim-trixie AS runtime

ARG VERSION

LABEL org.opencontainers.image.title="ddbj-search-api" \
    org.opencontainers.image.description="The implementation of DDBJ Search API" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.authors="Bioinformatics and DDBJ Center" \
    org.opencontainers.image.url="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.source="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.documentation="https://github.com/ddbj/ddbj-search-api/blob/main/README.md" \
    org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/home/app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# The venv is built by uv in builder, so the runtime does not need pip or the packages pip vendors.
RUN /usr/local/bin/python -m pip uninstall -y pip && \
    mkdir -p /home/app && chmod 777 /home/app

ENTRYPOINT []
CMD ["sleep", "infinity"]
