import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/components.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model JgaStudy {
  @example("jga-study")
  type: "jga-study";

  @example("JGAS000004")
  identifier: string;

  @example("JSUB000007_Study_0001")
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
  @example("https://ddbj.nig.ac.jp/resource/jga-study/JGAS000004")
  url: string;

  distribution: Distribution[];
  properties: unknown;
  sameAs: Xref[] | null;
  description: string | null;

  @example("Genomic and Genetic Analysis of Brain Tumors and Analysis of Their Clinicopathological Significance")
  title: string | null;

  downloadUrl: DownloadUrl[] | null;

  @doc(Caution.oldOrganism)
  organism: __Organism | null;

  @doc(Caution.dbXrefs)
  dbXrefs: Xref[];

  @doc(Caution.dbXrefsStatistics)
  dbXrefsStatistics: __DbXrefsStatistics[];
}
