# DDBJ-Search API

## Introduction

This repository contains the implementation of:

- **API Specification**
- **API Server**

for [GitHub - ddbj/ddbj-search](https://github.com/ddbj/ddbj-search).  
As of November 2024, both components are **Under Development**.

## API Specification

Not yet written.

## API Server

### Deployment

The API Server can be deployed using Docker and Docker Compose. Follow these steps:

```bash
docker network create ddbj-search-network
docker compose up -d --build
```

The `ddbj_search_api` application, executed internally, accepts the following options:

```bash
$ docker compose exec app ddbj_search_api --help
usage: ddbj_search_api [-h] [--host] [--port] [--debug] [--url-prefix] [--es-url]

DDBJ Search API

options:
  -h, --help     show this help message and exit
  --host         Host address for the service. (default: 127.0.0.1)
  --port         Port number for the service. (default: 8080)
  --debug        Enable debug mode.
  --url-prefix   URL prefix for the service endpoints. (default: '/search', e.g.,
                 /dfast/api)
  --base-url     Base URL for JSON-LD @id field. This field is generated using
                 the format: {base_url}/entry/bioproject/{bioproject_id}.jsonld.
                 (default: http://{host}:{port}{url_prefix})
  --es-url       URL for Elasticsearch resources. (default:
                 'https://ddbj.nig.ac.jp/search/resources')
```

While it is possible to configure these options directly, it is generally recommended to define them in the [compose.yml](./compose.yml) file as environment variables.

Example configuration in `compose.yml`:

```yaml
    environment:
      - DDBJ_SEARCH_API_DEBUG=False
      - DDBJ_SEARCH_API_HOST=0.0.0.0
      - DDBJ_SEARCH_API_PORT=8080
      - DDBJ_SEARCH_API_BASE_URL=https://dev.ddbj.nig.ac.jp/search
      - DDBJ_SEARCH_API_URL_PREFIX=/search
      - DDBJ_SEARCH_API_ES_URL=https://ddbj.nig.ac.jp/search/resources
```

### API Server Test

To verify that the API Server is functioning correctly, use the following test commands as examples:

```bash
# Retrieve BioProject data in JSON format
curl -X GET "http://localhost:8080/search/entry/bioproject/PRJNA16.json"

# Retrieve BioSample data in JSON format
curl -X GET "http://localhost:8080/search/entry/biosample/SAMN02953658.json"

# Retrieve BioProject data in JSON-LD format
curl -X GET "http://localhost:8080/search/entry/bioproject/PRJNA16.jsonld"

# Retrieve BioSample data in JSON-LD format
curl -X GET "http://localhost:8080/search/entry/biosample/SAMN02953658.jsonld"
```

### Development

To set up the development environment for the API Server, use the [compose.dev.yml](./compose.dev.yml) file. Follow these steps:

```bash
docker network create ddbj-search-network
docker compose -f compose.dev.yml up -d --build
docker compose -f compose.dev.yml exec app ddbj_search_api --debug
```

## License

This project is licensed under the [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) license. See the [LICENSE](./LICENSE) file for details.
