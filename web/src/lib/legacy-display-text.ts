/** Presentation only: retain original API/DB evidence and unknown custom text. */
const legacyAssetSummaries: Readonly<Record<string, string>> = Object.freeze({
  "\u4efb\u52a1\u6267\u884c\u671f\u95f4\u81ea\u52a8\u5173\u8054": "Automatically associated during task execution",
  "\u7531\u5386\u53f2\u4efb\u52a1\u8d44\u4ea7\u5173\u8054\u8fc1\u79fb": "Migrated from a historical task-asset association",
});

export function assetSummaryDisplay(text: string): string {
  return Object.hasOwn(legacyAssetSummaries, text) ? legacyAssetSummaries[text] : text;
}

export function retestErrorDisplay(text: string): string {
  return text === "\u590d\u6d4b\u4f1a\u8bdd\u5df2\u5220\u9664"
    ? "Retest conversation has been deleted"
    : text;
}
