FROM python:3.12-bookworm

LABEL org.opencontainers.image.title="ddbj-search-api" \
    org.opencontainers.image.description="The implementation of DDBJ Search API" \
    org.opencontainers.image.version="0.1.0" \
    org.opencontainers.image.authors="Bioinformatics and DDBJ Center" \
    org.opencontainers.image.url="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.source="https://github.com/ddbj/ddbj-search-api" \
    org.opencontainers.image.documentation="https://github.com/ddbj/ddbj-search-api/blob/main/README.md" \
    org.opencontainers.image.licenses="Apache-2.0"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt update && \
    apt install -y --no-install-recommends \
    curl \
    iputils-ping \
    jq \
    less \
    procps \
    tree \
    vim-tiny && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY ddbj_search_api ./ddbj_search_api

RUN uv sync --extra tests -P ddbj-search-converter

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT [""]
CMD ["sleep", "infinity"]
