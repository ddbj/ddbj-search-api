import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/models.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model BioSample {
  type: "biosample";
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
  sameAs: Xref[] | null;
  description: string | null;
  title: string | null;

  @doc(Descriptions.dbXref)
  dbXref: Xref[] | null;

  organism: Organism | null;
  attributes: {
    @doc(Descriptions.snake)
    attribute_name: string;

    @doc(Descriptions.snake)
    display_name: string;

    @doc(Descriptions.snake)
    harmonized_name: string;

    content: string;
  }[];
  `model`: {
    name: string;
  }[];

  @doc(Descriptions.pascal)
  Package: {
    name: string;

    @doc(Descriptions.snake)
    display_name: string;
  };
}
