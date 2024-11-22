import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/components.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model JgaDac {
  @example("jga-dac")
  type: "jga-dac";

  identifier: string;
  name: string | null;
  dateCreated: string;
  datePublished: string | null;
  dateModified: string;

  @doc(Todo.checkEnum)
  visibility: string;

  @doc(Todo.checkEnum)
  status: string;

  isPartOf: "jga";

  @doc(Descriptions.url)
  url: string;

  distribution: Distribution[];
  properties: unknown;
}
