from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DbType(str, Enum):
    bioproject = "bioproject"
    biosample = "biosample"
    sra_submission = "sra-submission"
    sra_study = "sra-study"
    sra_experiment = "sra-experiment"
    sra_run = "sra-run"
    sra_sample = "sra-sample"
    sra_analysis = "sra-analysis"
    jga_study = "jga-study"
    jga_dataset = "jga-dataset"
    jga_dac = "jga-dac"
    jga_policy = "jga-policy"


class KeywordsOperator(str, Enum):
    AND = "AND"
    OR = "OR"


class UmbrellaFilter(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"


class Pagination(BaseModel):
    page: int = Field(..., ge=1, examples=[1], description="Current page number")
    per_page: int = Field(
        ...,
        ge=1,
        le=100,
        alias="perPage",
        examples=[10],
        description="Number of items per page",
    )
    total: int = Field(..., ge=0, examples=[10000], description="Total number of items")


class Organism(BaseModel):
    identifier: Optional[str] = Field(None, examples=["9606"], description="Taxonomy ID")
    name: Optional[str] = Field(None, examples=["Homo sapiens"], description="Organism name")


class DbXref(BaseModel):
    identifier: str = Field(..., examples=["SAMN123"], description="Reference ID")
    type: DbType
    url: Optional[str] = Field(None, examples=["/entries/biosample/SAMN123"], description="Reference URL")


class ProblemDetails(BaseModel):
    type: str = Field("about:blank", examples=["about:blank"], description="Problem type URI")
    title: str = Field(..., examples=["Not Found"], description="Short description of the problem")
    status: int = Field(..., examples=[404], description="HTTP status code")
    detail: Optional[str] = Field(None, examples=["The requested BioProject 'INVALID' was not found."], description="Detailed description")
    instance: Optional[str] = Field(None, examples=["/entries/bioproject/INVALID"], description="Request path where the problem occurred")
