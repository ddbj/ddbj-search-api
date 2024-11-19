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
