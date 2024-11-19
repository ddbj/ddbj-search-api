import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/models.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model BioProject {
  type: "bioproject";
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

  @doc("UmbrellaProject を判断するために存在している")
  objectType: "UmbrellaBioProject" | "BioProject";

  accession: string;
  organization: {
    abbreviation: string;
    name: string;
    organizationType: string;
    role: string;
    url: string;
  }[];
  publication: {
    date: string;
    Reference: string | null;
    id: string;
    title: string;
    url?: string | null;
    DbType: string;
    status: string;
  }[];
  externalLink: {
    label: string;
    url: string;
  }[];
  grant: {
    title?: string;
    id: string;
    agency: {
      abbreviation: string;
      name: string;
    };
  }[];
}
