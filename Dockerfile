FROM python:3.10.15-slim-bookworm

WORKDIR /app
COPY . .
RUN python3 -m pip install --no-cache-dir --progress-bar off -U pip && \
    python3 -m pip install --no-cache-dir --progress-bar off -e .[tests]

ENV DDBJ_SEARCH_API_ES_URL https://dev.ddbj.nig.ac.jp/search/resources

EXPOSE 8080

CMD [ "sleep", "infinity" ]
