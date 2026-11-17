// Test-only original TypeScript loader; not a browser or Next client bundle.

import ts from "../node_modules/typescript/lib/typescript.js";
import { readFileSync, statSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = new URL("../src/lib/", import.meta.url);
function candidate(path) {
  for (const name of [path, `${path}.ts`, `${path}.mjs`, `${path}/index.ts`]) {
    try {
      if (statSync(name).isFile()) return name;
    } catch {
      // Normal extension resolution; unresolved imports use Node's default.
    }
  }
  return null;
}
registerHooks({
  resolve(specifier, context, nextResolve) {
    let url;
    if (specifier.startsWith("@/lib/")) url = new URL(specifier.slice(6), root);
    else if (specifier.startsWith(".") && context.parentURL?.startsWith(root.href))
      url = new URL(specifier, context.parentURL);
    if (url?.href.startsWith(root.href)) {
      const found = candidate(fileURLToPath(url));
      if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.startsWith(root.href) && url.endsWith(".ts")) {
      const source = readFileSync(fileURLToPath(url), "utf8");
      const result = ts.transpileModule(source, {
        fileName: fileURLToPath(url),
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      });
      return { format: "module", source: result.outputText, shortCircuit: true };
    }
    return nextLoad(url, context);
  },
});
