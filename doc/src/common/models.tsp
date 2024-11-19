model Distribution {
  contentUrl: string;
  encodingFormat: string;
  type: string;
}

model Xref {
  identifier: string;
  type: string;
  url: string;
}

model Organism {
  identifier: string;
  name: string | null;
}
