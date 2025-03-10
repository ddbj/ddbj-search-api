from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# === BioProject ===


class Distribution(BaseModel):
    contentUrl: str
    encodingFormat: str
    type_: str = Field(alias="type")


class Xref(BaseModel):
    identifier: str
    type_: str = Field(alias="type")
    url: str


class Organism(BaseModel):
    identifier: str
    name: Optional[str]


class Organization(BaseModel):
    abbreviation: str
    name: str
    organizationType: str
    role: str
    url: str


class Publication(BaseModel):
    date: str
    Reference: Optional[str]
    id_: str = Field(alias="id")
    title: str
    url: Optional[str]
    DbType: str
    status: str


class ExternalLink(BaseModel):
    label: str
    url: str


class Agency(BaseModel):
    abbreviation: str
    name: str


class Grant(BaseModel):
    title: Optional[str]
    id_: str = Field(alias="id")
    agency: List[Agency]


class BioProject(BaseModel):
    type_: Literal["bioproject"] = Field(alias="type")
    identifier: str
    name: Optional[str]
    dateCreated: str
    datePublished: Optional[str]
    dateModified: str
    visibility: str
    status: str
    isPartOf: Literal["BioProject"]
    url: str
    distribution: List[Distribution]
    properties: Any
    sameAs: Union[List[Xref], None]
    description: Optional[str]
    title: Optional[str]
    dbXref: Union[List[Xref], None]
    organism: Union[Organism, None]
    objectType: Literal["UmbrellaBioProject", "BioProject"]
    accession: str
    organization: List[Organization]
    publication: List[Publication]
    externalLink: List[ExternalLink]
    grant: List[Grant]


class BioProjectLD(BioProject):
    context: str = Field(alias="@context")
    id_: str = Field(alias="@id")

    model_config = ConfigDict(
        populate_by_name=True
    )


# === BioSample ===


class Attribute(BaseModel):
    attribute_name: str
    display_name: str
    harmonized_name: str
    content: str


class Model(BaseModel):
    name: str


class Package(BaseModel):
    name: str
    display_name: str


class BioSample(BaseModel):
    type_: Literal["biosample"] = Field(alias="type")
    identifier: str
    name: Optional[str]
    dateCreated: str
    datePublished: Optional[str]
    dateModified: str
    visibility: str
    status: str
    isPartOf: str
    url: str
    distribution: List[Distribution]
    properties: Any
    sameAs: Union[List[Xref], None]
    description: Optional[str]
    title: Optional[str]
    dbXref: Union[List[Xref], None]
    organism: Union[Organism, None]
    attributes: List[Attribute]
    model: List[Model]
    Package: Package


class BioSampleLD(BioSample):
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
