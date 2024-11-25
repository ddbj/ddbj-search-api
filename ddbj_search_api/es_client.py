from importlib.metadata import version
from typing import Any, Literal, Union

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ddbj_search_api.config import get_config


class ESGetDocResponse(BaseModel):
    _index: str
    _type: Literal["_doc"]
    _id: str
    found: bool
    source: Any = Field(alias="_source")

    model_config = ConfigDict(
        extra="allow"
    )


def user_agent() -> str:
    return f"ddbj-search-api/{version('ddbj-search-api')}"


async def get_es_doc(index: Literal["bioproject", "biosample"], id_: str) -> Union[Any, None]:
    """\
    Request to {es_url}/{index}/_doc/{id_}

    Return: BioProject, BioProject, or None

    Note:

    - return None -> Not found
    - Please cast the return value to BioProject or BioSample after this function
    """
    app_config = get_config()
    es_url = app_config.es_url.rstrip("/")

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{es_url}/{index}/_doc/{id_}",
                timeout=10,
                follow_redirects=True,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": user_agent(),
                    "Accept": "*/*",
                },
            )
            if res.status_code == 404:
                return None
            res.raise_for_status()
            es_res = ESGetDocResponse.model_validate(res.json())
            if not es_res.found:
                return None
            return es_res.source
    except Exception as e:
        raise Exception(f"Failed to get Elasticsearch document from {es_url}/{index}/_doc/{id_}: {e}") from e  # pylint: disable=broad-exception-raised
