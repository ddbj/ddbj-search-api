import "@typespec/http";
import "@typespec/rest";
import "@typespec/json-schema";
import "./common/models.tsp";
import "./dbsets/BioProject.tsp";
import "./dbsets/BioSample.tsp";
import "./dbsets/JgaDac.tsp";

using TypeSpec.JsonSchema;
using TypeSpec.Http;
using TypeSpec.Rest;

@service({
  title: "DDBJ SEARCH API",
})
namespace DDBJSearch;

@jsonSchema
union SearchResponse {
  BioProject,
  BioSample,
  JgaDac,
}

@post op detail(): SearchResponse;
