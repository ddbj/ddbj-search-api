import "@typespec/http";
import "@typespec/rest";
import "@typespec/json-schema";
import "@typespec/openapi3";
import "./dbsets/BioProject.tsp";
import "./dbsets/BioSample.tsp";
import "./dbsets/JgaStudy.tsp";
import "./dbsets/JgaDac.tsp";

using TypeSpec.JsonSchema;
using TypeSpec.Http;
using TypeSpec.Rest;
using TypeSpec.OpenAPI;

@service({
  title: "DDBJ SEARCH API",
})
@server("https://ddbj.nig.ac.jp/search/resources/", "production")
@server("https://dev.ddbj.nig.ac.jp/search/resources/", "development")
namespace DDBJSearch;

@jsonSchema
@TypeSpec.JsonSchema.oneOf
@TypeSpec.OpenAPI.oneOf
union SearchResponse {
  BioProject,
  BioSample,
  JgaStudy,
  JgaDac,
}
@route("jga-*,sra-*,bioproject,biosample/_msearch")
interface SearchResources {
  @post
  search(
    @header `Content-Type`: "application/json; charset=UTF-8",
    @body SearchRequest: "",
  ): SearchResponse;
}
