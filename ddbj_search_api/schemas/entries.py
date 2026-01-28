from typing import Generic, List, Optional, TypeVar, Union

from ddbj_search_converter.schema import JGA, SRA, BioProject, BioSample
from pydantic import BaseModel, ConfigDict, Field

from ddbj_search_api.schemas.common import DbType, DbXref, Organism, Pagination

T = TypeVar("T")

ConverterEntry = Union[BioProject, BioSample, SRA, JGA]

DB_TYPE_TO_ENTRY_MODEL: dict[DbType, type[BaseModel]] = {
    DbType.bioproject: BioProject,
    DbType.biosample: BioSample,
    DbType.sra_submission: SRA,
    DbType.sra_study: SRA,
    DbType.sra_experiment: SRA,
    DbType.sra_run: SRA,
    DbType.sra_sample: SRA,
    DbType.sra_analysis: SRA,
    DbType.jga_study: JGA,
    DbType.jga_dataset: JGA,
    DbType.jga_dac: JGA,
    DbType.jga_policy: JGA,
}


class EntryListItem(BaseModel):
    identifier: str = Field(..., examples=["PRJNA16"], description="Entry ID")
    type: DbType
    title: str = Field(..., examples=["Cancer Genome Project"], description="Title")
    organism: Optional[Organism] = None
    date_published: str = Field(
        ...,
        alias="datePublished",
        examples=["2013-05-31"],
        description="Publication date",
    )
    db_xrefs: Optional[List[DbXref]] = Field(None, alias="dbXrefs", description="Related database references")

    model_config = ConfigDict(populate_by_name=True)


class EntryListResponse(BaseModel, Generic[T]):
    pagination: Pagination
    items: List[T]


class EntryDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    identifier: Optional[str] = Field(None, description="Entry ID")
    type: Optional[DbType] = None
    title: Optional[str] = Field(None, description="Title")
    description: Optional[str] = Field(None, description="Description")
    organism: Optional[Organism] = None
    date_created: Optional[str] = Field(None, alias="dateCreated", description="Creation date")
    date_modified: Optional[str] = Field(None, alias="dateModified", description="Modification date")
    date_published: Optional[str] = Field(None, alias="datePublished", description="Publication date")


class EntryDetailJsonLd(EntryDetail):
    context: str = Field(..., alias="@context", description="JSON-LD context URL")
    id_: str = Field(..., alias="@id", description="Entry URI")

    model_config = ConfigDict(extra="allow", populate_by_name=True)
