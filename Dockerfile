FROM python:3.12-bookworm

ARG VERSION=dev

LABEL org.opencontainers.image.title="ddbj-search-api" \
    org.opencontainers.image.description="The implementation of DDBJ Search API" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.authors="Bioinformatics and DDBJ Center" \
    org.opencontainers.image.url="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.source="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.documentation="https://github.com/ddbj/ddbj-search-api/blob/main/README.md" \
    org.opencontainers.image.licenses="Apache-2.0"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --extra tests --no-install-project -P ddbj-search-converter && \
    chmod -R a+rwX .venv

COPY . .

RUN SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION} uv sync --extra tests -P ddbj-search-converter

ENV HOME=/home/app
RUN mkdir -p /home/app && chmod 777 /home/app

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT []
CMD ["sleep", "infinity"]
