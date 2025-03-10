from ddbj_search_converter.schema import BioProject, BioSample  # type: ignore
from pydantic import BaseModel, ConfigDict, Field

# === BioProject ===


class BioProjectLD(BioProject):  # type: ignore
    context: str = Field(alias="@context")
    id_: str = Field(alias="@id")

    model_config = ConfigDict(
        populate_by_name=True
    )


# === BioSample ===


class BioSampleLD(BioSample):  # type: ignore
    context: str = Field(alias="@context")
    id_: str = Field(alias="@id")

    model_config = ConfigDict(
        populate_by_name=True
    )


# === Other ===


class ErrorResponse(BaseModel):
    msg: str = Field(
        ...,
        examples=["Internal server error"],
        description="The error message.",
    )
    status_code: int = Field(
        ...,
        examples=[500],
        description="The status code of the error.",
    )
