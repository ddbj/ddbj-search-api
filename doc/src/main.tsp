import "@typespec/http";
import "@typespec/rest";
import "@typespec/json-schema";

using TypeSpec.JsonSchema;
using TypeSpec.Http;
using TypeSpec.Rest;

@service({
  title: "DDBJ SEARCH API",
})
namespace DemoService;

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

@jsonSchema
model BioProject extends BaseResponse {
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
