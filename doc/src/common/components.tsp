@friendlyName("components:Distribution")
model Distribution {
  contentUrl: string;
  encodingFormat: string;
  type: string;
}

@friendlyName("components:Xref")
model Xref {
  identifier: string;
  type: string;
  url: string;
}

@friendlyName("components:Organism")
model Organism {
  identifier: string;
  name: string | null;
}

@friendlyName("components:DownloadUrl")
model DownloadUrl {
  name: string;
  ftpUrl: string;
  type: string;
  url: string;
}

@friendlyName("deprecated:DbXrefsStatistics")
model __DbXrefsStatistics {
  count: integer;
  type: string;
}
@friendlyName("deprecated:OldOrganism")
model __Organism {
  identifier: integer;
  name: string | null;
}
