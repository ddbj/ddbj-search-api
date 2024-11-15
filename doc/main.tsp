import "@typespec/http";
import "@typespec/rest";

@service({
  title: "DDBJ SEARCH API",
})
namespace DemoService;

using TypeSpec.Http;
using TypeSpec.Rest;

model Distribution {}

model BaseResponse {
  identifier: string;
  visibility: string;
  isPartOf: string;
  distribution: Distribution[];
  url: string;
  name: string | null;
  properties: {} | null;
  status: string;
  dateCreated: string;
  datePublished: string | null;
  dateModified: string;
}

model DBXref {
  identifier: string;
  type: string;
  url: string;
}

model BioProject extends OmitProperties<BaseResponse, "url"> {
  type: "BioProject";
  objectType: "UmbrellaBioProject" | "BioProject";
  description: string | null;
  title: string | null;
  accession: string;

  @doc("dbXrefs ではない")
  dbXref: DBXref[] | null;

  id: string;
}
model BioSample extends OmitProperties<BaseResponse, "status"> {
  type: "BioSample";
}

union SearchResponse {
  BioProject,
  BioSample,
}

@post op detail(): SearchResponse;
