union Descriptions {
  url: "DDBJ Search の詳細画面URL",
}

union Caution {
  dbXref: "[CAUTION] 新APIへの移行につき `dbXrefs` から `dbXref` (複数->単数)に変わった",
  dbXrefs: "[CAUTION] 新APIへの移行前。新APIでは `dbXref` (複数->単数)に変更されるべき",
  dbXrefsStatistics: "[CAUTION] 新APIでは不要。dbXrefの内容から集計できる。",
  oldOrganism: "[CAUTION] 旧APIでは identifier が integer だが、新APIでは string に変更する",
}

union Todo {
  pascal: "[TODO] PascalCasingなのが気になる",
  snake: "[TODO] SnakeCasingなのが気になる",
  checkEnum: "[TODO] StringではなくEnumの候補を確認する",
}
