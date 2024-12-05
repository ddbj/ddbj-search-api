import "@typespec/json-schema";
import "../common/constants.tsp";
import "../common/components.tsp";

using TypeSpec.JsonSchema;

@jsonSchema
model SraSample {
  @example("sra-sample")
  type: "sra-sample";

  @example("ERS2785606")
  identifier: string;

  @example("SAMEA4967388")
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

  @example("sra")
  isPartOf: "sra";

  @doc(Descriptions.url)
  @example("https://ddbj.nig.ac.jp/resource/sra-sample/ERS2785606")
  url: string;

  distribution: Distribution[];
  properties: unknown;
  sameAs: Xref[] | null;
  description: string | null;

  @example("pneuDEEP7565425")
  title: string | null;

  downloadUrl: DownloadUrl[] | null;

  @doc(Caution.oldOrganism)
  organism: __Organism | null;

  @doc(Caution.dbXrefs)
  dbXrefs: Xref[];

  @doc(Caution.dbXrefsStatistics)
  dbXrefsStatistics: __DbXrefsStatistics[];
}
