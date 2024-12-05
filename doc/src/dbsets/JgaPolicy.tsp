import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/components.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model JgaPolicy {
  @example("jga-policy")
  type: "jga-policy";

  @example("JGAP000003")
  identifier: string;

  @example("JGAP000003")
  name: string | null;

  @example("2014-09-08T08:23:12+09:00")
  dateCreated: string;

  @example("2014-09-08T08:23:12+09:00")
  datePublished: string | null;

  @example("2014-09-08T08:23:12+09:00")
  dateModified: string;

  @doc(Todo.checkEnum)
  @example("unrestricted-access")
  visibility: string;

  @doc(Todo.checkEnum)
  @example("public")
  status: string;

  @example("jga")
  isPartOf: "jga";

  @doc(Descriptions.url)
  @example("https://ddbj.nig.ac.jp/resource/jga-policy/JGAP000003")
  url: string;

  distribution: Distribution[];
  properties: unknown;
  sameAs: Xref[] | null;
  description: string | null;

  @example("Company User Limit")
  title: string | null;

  downloadUrl: DownloadUrl[] | null;

  @doc(Caution.oldOrganism)
  organism: __Organism | null;

  @doc(Caution.dbXrefs)
  dbXrefs: Xref[];

  @doc(Caution.dbXrefsStatistics)
  dbXrefsStatistics: __DbXrefsStatistics[];
}
