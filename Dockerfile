FROM python:3.10.15-slim-bookworm

LABEL org.opencontainers.image.authors="Bioinformatics and DDBJ Center"
LABEL org.opencontainers.image.url="https://github.com/ddbj/ddbj-search-api"
LABEL org.opencontainers.image.source="https://github.com/ddbj/ddbj-search-api/blob/main/Dockerfile"
LABEL org.opencontainers.image.version="0.1.0"
LABEL org.opencontainers.image.description="The implementation of DDBJ Search API"
LABEL org.opencontainers.image.licenses="Apache2.0"

WORKDIR /app
COPY . .
RUN python3 -m pip install --no-cache-dir --progress-bar off -U pip && \
    python3 -m pip install --no-cache-dir --progress-bar off -e .[tests]

ENV DDBJ_SEARCH_API_DEBUG False
ENV DDBJ_SEARCH_API_HOST 0.0.0.0
ENV DDBJ_SEARCH_API_PORT 8080
ENV DDBJ_SEARCH_API_URL_PREFIX /search
ENV DDBJ_SEARCH_API_BASE_URL https://ddbj.nig.ac.jp/search
ENV DDBJ_SEARCH_API_ES_URL https://ddbj.nig.ac.jp/search/resources

EXPOSE 8080

CMD [ "ddbj_search_api" ]
