import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/components.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model JgaDac {
  type: "jga-dac";
  identifier: string;
  name: string | null;
  dateCreated: string;
  datePublished: string | null;
  dateModified: string;
  visibility: string;
  status: string;
  isPartOf: string;

  @doc(Descriptions.url)
  url: string;

  distribution: Distribution[];
  properties: unknown;
}
