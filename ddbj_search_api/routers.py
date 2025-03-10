from fastapi import APIRouter, HTTPException, status

from ddbj_search_api.config import (BIOPROJECT_CONTEXT_URL,
                                    BIOSAMPLE_CONTEXT_URL, get_config)
from ddbj_search_api.es_client import get_es_doc
from ddbj_search_api.schemas import (BioProject, BioProjectLD, BioSample,
                                     BioSampleLD)

router = APIRouter()


# http://localhost:8080/search/entry/bioproject/PRJNA16.json
# https://dev.ddbj.nig.ac.jp/search/entry/bioproject/PRJNA16.json
@router.get(
    "/entry/bioproject/{bioproject_id}.json",
    response_model=BioProject,
)
async def get_bioproject_json(bioproject_id: str) -> BioProject:
    doc = await get_es_doc("bioproject", bioproject_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The request BioProject ID '{bioproject_id}' was not found."
        )

    return BioProject(**doc)


# http://localhost:8080/search/entry/biosample/SAMN04070179.json
# https://dev.ddbj.nig.ac.jp/search/entry/biosample/SAMN04070179.json
@router.get(
    "/entry/biosample/{biosample_id}.json",
    response_model=BioSample,
)
async def get_biosample_json(biosample_id: str) -> BioSample:
    doc = await get_es_doc("biosample", biosample_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The request BioSample ID '{biosample_id}' was not found."
        )

    return BioSample(**doc)


# http://localhost:8080/search/entry/bioproject/PRJNA16.jsonld
# https://dev.ddbj.nig.ac.jp/search/entry/bioproject/PRJNA16.jsonld
@router.get(
    "/entry/bioproject/{bioproject_id}.jsonld",
    response_model=BioProjectLD,
)
async def get_bioproject_jsonld(bioproject_id: str) -> BioProjectLD:
    doc = await get_es_doc("bioproject", bioproject_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The request BioProject ID '{bioproject_id}' was not found."
        )

    app_config = get_config()
    resource_uri = f"{app_config.base_url}/entry/bioproject/{bioproject_id}.jsonld"

    return BioProjectLD(
        **{
            "@context": BIOPROJECT_CONTEXT_URL,
            "@id": resource_uri,
            **doc
        }
    )


# http://localhost:8080/search/entry/biosample/SAMN04070179.jsonld
# https://dev.ddbj.nig.ac.jp/search/entry/biosample/SAMN04070179.jsonld
@router.get(
    "/entry/biosample/{biosample_id}.jsonld",
    response_model=BioSampleLD,
)
async def get_biosample_jsonld(biosample_id: str) -> BioSampleLD:
    doc = await get_es_doc("biosample", biosample_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The request BioSample ID '{biosample_id}' was not found."
        )

    app_config = get_config()
    resource_uri = f"{app_config.base_url}/entry/biosample/{biosample_id}.jsonld"

    return BioSampleLD(
        **{
            "@context": BIOSAMPLE_CONTEXT_URL,
            "@id": resource_uri,
            **doc
        }
    )
