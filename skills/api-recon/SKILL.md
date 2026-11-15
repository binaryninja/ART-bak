---
name: api-recon
description: Invoke this skill when collecting a website's API interfaces.
---

# API Recon (Frontend Interface Reconnaissance)

With **authorization**, discover as completely as possible: **backend APIs** (paths, methods, parameters, response bodies), **frontend routes**, and **UI feature triggers** (tabs, dialogs, table actions, etc.).

---

## Boundaries and Prohibitions (Agents Must Read; Violations Exceed Scope)

This skill performs **API / parameter-surface reconnaissance only**, not vulnerability discovery or exploitation.

### Task Boundaries

| Scope | Allowed | Prohibited |
|---|---|---|
| **Goal** | Enumerate paths, methods, parameters, routes, and UI triggers | SQLi/XSS/access-control/brute-force/fuzz vulnerability testing, request-tampering attacks, destructive operations |
| **Authentication** | Hooks + stubs/mocks to bypass **client-side** login gates | Asking users for or guessing credentials; attempting real login-form submissions |
| **Runtime** | Hook interfaces without credentials; use mock responses to reach the SPA's post-login shell | Workflows that require a real backend session to continue |

### Credential-Free Dynamic Analysis (Phase 3 Default)

1. **Intercept and stub** login, permission, menu, and other bootstrap interfaces using `preload.js` / `runtime_harvest.js`;
2. Return mock bodies for business-query interfaces with **correct structure, successful business codes, and optionally empty data**;
3. Let the frontend render post-login pages even without a backend or with 401 responses, triggering additional XHR/fetch/WebSocket requests;
4. **Empty data, blank tables, and placeholder UI are expected**. Do not switch to real login or vulnerability testing because of them.

**In short**: use mocks to open frontend routes and mount components; **record only outbound requests**. Backend responses are not the point: discover **which additional interfaces the frontend calls**.

### Hard Workflow Prohibitions

| Prohibited | Alternative |
|---|---|
| Using grep/curl/Read on the main `index-*.js` entry to extract API paths before Phase 1 completes | Run `OUTDIR/harvest_static.py` |
| Writing `extract_apis.py` or other scripts to replace harvest | Modify `OUTDIR/harvest_static.py` and rerun |
| Repeating the same grep/command after ≥2 failures | Change strategy: read tool_logs, modify harvest, consult reference |
| Skipping gates A/B and running original `scripts/` files directly | Copy to OUTDIR and adapt to the target |
| Real username/password, OTP, OAuth, or other authentication | Stub/mock (see above) |
| Skipping stubs to perform access-control/injection testing to "get real data" | Record outbound only; remain within recon scope |
| Irreversible actions such as deletion, sensitive-data export, or bulk writes | The same restriction applies to coverage clicks |
| Claiming all pages/interfaces were found without runtime + dynamic enumeration | Follow the Definition of Done or state limitations |
| Claiming all parameters are known without a parameter-trigger matrix + diff | Phase 3b matrix + Phase 5 diff |
| Inferring required/optional fields from one runtime sample | Multi-sample diff, validation rules, or inference from errors |

---

## Two-Layer Model and Runtime Modes

| Layer | Output | Limitations |
|---|---|---|
| **Static** (JS bundles) | Full endpoint paths, draft routes, candidate request-construction fields | No HTTP methods; parameters require Phase 1b; runtime-composed URLs may be missed |
| **Runtime** (live session) | Methods + bodies + responses + dynamic URLs + WS/SSE; multi-sample diffs complete parameters | Pages must actually render to send requests; one sample cannot establish required/optional fields |

| Runtime Mode | Engine | Suitable For |
|---|---|---|
| **depth** | `runtime_harvest.js` (Puppeteer) | API inventories, METHOD/params/responses, WS/SSE, reproducible batch runs |
| **coverage** | browser + `preload.js` | Clicking tabs/dialogs/tables for deeper feature coverage |
| **both** | depth followed by coverage | Most complete; takes longest |

**Parameter methodology** (no universal script): use harvest/regex for paths; use **expanded anchor windows + UI binding chains + multi-sample diffs + error inference** for parameters (grep recipes: [reference.md](reference.md), section J).

---

## Definition of Done

Claim recon is complete only when all apply:

- [ ] **Static**: Phase 1 harvest produced `api_static.txt`, `routes.txt`, and `js/`
- [ ] **Runtime**: at least depth or coverage; coverage/both require **working hooks + a dynamic enumeration loop**
- [ ] **Application shell**: visiting business paths does not land on `/login` (watch hash routing)
- [ ] **Parameters**: coverage/both completed the trigger matrix + `param_samples.json`; Phase 5 merged `params_merged.json`
- [ ] **Depth** (if module pages are blank): reconstruct the permission tree in Phase 4 and rerun until **module-level APIs** appear, not just locale/bootstrap
- [ ] **Delivery**: all Phase 5 outputs exist (see output table); `insert_assets` wrote service and endpoint assets

---

## Scripts and Gates

`scripts/` contains reference templates only. **Do not** run originals directly and treat them as final results.

**Rule**: read → adapt to the target → write to `OUTDIR` (such as `recon/`) → record `CHANGES.md`; if unsuitable, rewrite using the methodology, borrowing only the structure.

| Gate | When | Reference Script → OUTDIR Copy | Common Required Changes |
|---|---|---|---|
| **A (static)** | After Phase 0, before the **first** harvest/spider run | `harvest_static.py` / `spider_mpa.py` | **Default regex works for most sites**; change endpoint regex, webpack/Vite `publicPath`, or MPA exclude/cookie only when manifest/dialect does not match |
| **B (runtime)** | After Phase 2, before depth/coverage | `runtime_harvest.js` / `preload.js` + `config.json` | Cookie/localStorage keys, neutralize success values, stubs, login regex, API prefixes, hash/history |

**Mandatory SPA order** (do not reorder; phase numbers override "explore before scripting"):

| Step | Required | Prohibited |
|---|---|---|
| After Phase 0 | Next Bash command = `python3 OUTDIR/harvest_static.py <URL> OUTDIR` | curl/grep/Read on the main `index-*.js` entry (usually >500KB) |
| Gate A | Copy script → make small changes if needed → **run immediately** | Manually extract APIs before deciding whether to harvest |
| Before Phase 1 completes | Check output with `wc -l`; fix harvest and retry 404s | Write extraction scripts; repeatedly grep undownloaded URLs |
| From Phase 1b | grep only `OUTDIR/js/*.js` | Substitute the main bundle for harvest |

- ✅ Copy `harvest_static.py` → (optionally) change regex → **run immediately**
- ❌ curl main bundle → repeated grep → temporary extraction script → harvest last
- **MPA**: next Bash command after Phase 0 = `python3 OUTDIR/spider_mpa.py ...`

---

## Tool and Output Constraints

| Constraint | Explanation |
|---|---|
| Large files | **Do not** Read/grep `index-*.js` >100KB into context; batch-process with OUTDIR scripts |
| grep output | Must use `\| head -20` or `-m 5`; keep only path summaries in conversation, not bundle fragments |
| Validation | Use `wc -l`, `ls \| wc -l`; do not Read whole directories |
| Initial regex exploration | Optional, ≤1 attempt, only small chunks ≤50KB or HTML; harvest is authoritative for formal static collection |
| Reference | Recipes/templates/troubleshooting: [reference.md](reference.md); do not duplicate it inline |

---

## Execution Roadmap

```
Phase 0 classify + OUTDIR
  → Gate A → Phase 1 harvest (★ run immediately ★)
  → Phase 1b parameter reverse engineering
  → Phase 2 three authentication gates → config.json
  → Gate B → Phase 3 runtime + parameter matrix
  → Phase 4 permission tree (if needed) → rerun Phase 3
  → Phase 5 merged report + insert_assets: batch-insert every discovered service and API endpoint asset; never omit discovered assets during insertion
```

Check off in order; **do not enter the next phase before completing the previous one**.

1. [ ] **Phase 0**: initial SPA/MPA classification; create `OUTDIR` → [Phase 0](#phase-0--classification)
2. [ ] **Gate A + Phase 1**: copy script → harvest **immediately** → validate with `wc -l` → [Phase 1](#phase-1--static)
3. [ ] **Phase 1b**: expanded anchor windows + binding layer → `param_candidates.json` → [Phase 1b](#phase-1b--parameter-reverse-engineering)
4. [ ] **Phase 2**: three authentication gates → `config.json` → [Phase 2](#phase-2--three-authentication-gates)
5. [ ] **Gate B**: adapt runtime scripts → [Phase 3](#phase-3--runtime)
6. [ ] **Phase 3**: depth / coverage / both; verify shell access; parameter-trigger matrix → `param_samples.json`
7. [ ] **Phase 4** (if needed): permission tree → patch stubs → rerun Phase 3 → [Phase 4](#phase-4--permission-tree-reconstruction)
8. [ ] **Phase 5**: merged outputs + report + `insert_assets` → [Phase 5](#phase-5--merge-and-report)

---

## Phase 0 — Classification

Fetch entry HTML and **create `OUTDIR`** (do not modify the skill's `scripts/`):

- **SPA**: empty shell + `<div id=app>` + chunks → Phases 1–5
- **MPA**: SSR + `<form>`, no endpoint bundle → after Gate A:

```bash
python3 recon/spider_mpa.py <BASE_URL> <OUTDIR> [--cookie "session=..."] [--max 300] [--depth 5] [--exclude "logout|delete|destroy"]
```

Produces `forms.txt`, `links.txt`, and `api_inline.txt`. If SPA forms ≈ 0 → switch to Phase 1.

---

## Phase 1 — Static

Follow [Scripts and Gates](#scripts-and-gates) · [Tool and Output Constraints](#tool-and-output-constraints).

```bash
python3 recon/harvest_static.py <BASE_URL> <OUTDIR>
```

harvest: parse HTML scripts → webpack/Vite manifest → download all lazy chunks → produce `js/`, `api_static.txt`, `routes.txt`, and `chunkmap.txt`.

```bash
wc -l OUTDIR/api_static.txt OUTDIR/routes.txt
ls OUTDIR/js | wc -l
```

- Compare chunk count with manifest: fix harvest and retry 404s; do not curl chunks individually
- Too few entries in `api_static.txt` → relax endpoint regex in OUTDIR and rerun (see reference)

### Phase 1b — Parameter Reverse Engineering

Paths come from Phase 1; parameter fields require separate recon. For grep rules, see [Tool and Output Constraints](#tool-and-output-constraints).

**Completion criterion**: for important interfaces, answer field name, transport location, inferred type, required status, sample value, and confidence.

#### 1b.0 — Transport Forms

| Form | Parameter Location | Static Inspection Priority |
|---|---|---|
| REST JSON | body + query | `(params\|data\|body)\s*:\s*\{` near path anchors |
| GraphQL | `variables` | gql templates, `$page: Int` |
| Traditional form | urlencoded | `<form>`, `FormData` |
| File upload | multipart | `FormData.append` |
| Path parameters | `/user/:id` | Route table + `useParams` / `$route.params` |
| Encryption/signing | Wrapped in `sign`/`data` | Hook encryption-function inputs (reference section D) |

Output: annotate each interface with `transport: query|json|form|graphql|encrypted`.

#### 1b.1 — Expanded Anchor Windows

Anchor on known paths and expand the context window to find request-construction objects:

```bash
grep -n '"/api/user/list"' OUTDIR/js/*.js | head -20
grep -rhoaE '.{0,120}("/api[^"]+").{0,200}' OUTDIR/js/*.js | head -20
grep -rhoaE '(params|data|body|payload)\s*:\s*\{' OUTDIR/js/*.js | head -20
```

| Wrapper Layer | Parameter Clues |
|---|---|
| axios instance | `data` / `params` |
| Unified request wrapper | Interceptors inject global fields |
| OpenAPI client | Generated method signatures |
| React Query / SWR | Second hook argument |
| Vue composable | Composable inputs |

Type remnants: `yup`/`zod`/rules, `Form.Item name=`, embedded Swagger.

→ `param_candidates.json`：`{ path, fields[], source: "static-callsite", confidence }`

#### 1b.2 — Binding Layer

```
Form field → onFinish/handleSubmit → transform → API payload
```

| Binding Source | Technique |
|---|---|
| Form submit | Follow submit → transform → API |
| Table search | `getFieldsValue()` → `params` |
| Route | `:id` / `?tab=` |
| Interceptor | Global `tenantId`, pagination, sign |
| Enum select | `options` → API enumeration values |

Trace up the DevTools call stack from `fetch`/`XHR.send` to request-construction functions.

#### 1b.3 — Three Request-Construction Questions (Not Phase 2's Three Authentication Gates)

| Question | What to Answer |
|---|---|
| **Assembly** | Where payload is built; transformation traces |
| **Validation** | required, pattern, enum |
| **Transport** | path / query / body / multipart / headers |

At the interceptor gate (Phase 2), also inspect globally injected fields (Authorization, `X-Tenant-Id`, sign).

#### 1b.4 — Connecting to Phase 3

Candidate fields come from static/binding analysis; **required/optional/conditional dependencies** require the Phase 3 parameter matrix + diffs + Phase 5 error inference.

---

## Phase 2 — Three Authentication Gates

grep in `OUTDIR/js/` (with `head`) and write `config.json` (recipes in reference):

| Gate | Question | Keywords |
|---|---|---|
| **Rendering gate** | How is logged-in state determined? | `isLogin`, `getToken`, Cookie/localStorage |
| **Interceptor gate** | What triggers a redirect to `/login`? | `response_code`, `errno`, axios interceptor |
| **Content gate** | Where do menus/permissions come from? | `menu`, `permission`, `role`, `acl`, `routes` |

Do not treat localStorage key names as credentials; confirm from chunks/request chains.

**Exit = Gate B**: capture conclusions in `config.json` and adapt `OUTDIR/runtime_harvest.js` / `preload.js`.

### Phase 2b — API Observation (Optional)

Use OUTDIR's `preload.js` to confirm session key names, Authorization, and nested API URLs:

| Configuration | Output |
|---|---|
| `recordDetail: true` | `__API_RECON_DETAIL__` |
| `observe.xhrHeaders: true` | Header observations |
| `extractUrlsFromResponse: true` | Nested APIs in responses |
| `observe.storageReads/cookieReads: true` | Backfill config |
| `neutralizeVueRouter: true` | `__API_RECON_ROUTES__` |

Export each coverage round: `__API_RECON_LOG__`, `__API_RECON_DETAIL__`, `__API_RECON_ROUTES__`, and `__API_RECON_OBSERVE__`.

---

## Phase 3 — Runtime

Gate B must be complete; follow [Boundaries and Prohibitions](#boundaries-and-prohibitions-agents-must-read-violations-exceed-scope) and the credential-free mock strategy.

Set `"runtimeMode": "depth" | "coverage" | "both"` in `config.json` (template in reference).

### Hooks and Stubs (Shared by depth + coverage)

| Layer | Scope | Purpose |
|---|---|---|
| L1 exact | Auth/permission/bootstrap stubs | Pass initial-page authentication |
| L2 negative-response correction | All JSON responses | Not-logged-in code → success |
| L3 fallback | `/api` and similar requests not matched by L1 | Empty success body to render UI |

- **depth**: fake auth + `forward` business-code rewriting + `stubs`; traverse `routes` (hash/history); produce `runtime_api.json`
- **coverage**: inject `preload.js` at **document-start** (CDP `addScriptToEvaluateOnNewDocument` or Userscript)

Verify: `window.__API_RECON_PRELOAD__` exists; business paths do not return to `/login`.

```bash
cd recon && npm install
node runtime_harvest.js config.json
```

### 3b — Dynamic coverage Enumeration (Required)

1. Main navigation/sidebar: click each item, wait 1–3s for network
2. Tab — `role=tab`、`.ant-tabs-tab`
3. Tables: view/edit/details on the first row
4. Toolbar: export, filter, create (**avoid irreversible deletion**)
5. After entering each module: merge APIs/routes
6. SPA: controlled `pushState` for uncovered paths in `routes.txt` (prohibited for MPA)

**Parameter-trigger matrix** (required): record each operation type once per module and **diff multiple samples**:

| Operation | Typical Additional Parameters |
|---|---|
| Initial list page | Pagination + default filters |
| Search click | keyword, filter |
| Advanced filters | More optional fields |
| Create/edit | Complete entity |
| Bulk/export/sort | `ids[]`, `exportType`, `sortField` |

**Outbound bodies/headers remain real under stubs**: requests are authoritative. Record → `scan_raw.json`, `param_samples.json`, `api_detail.json`.

- **Vue**：`neutralizeVueRouter: true` + document-start preload
- **React**: `routes.txt` + sidebar clicks + `pushState`
- **both**: 3a depth first, then 3b coverage

---

## Phase 4 — Permission Tree Reconstruction

**Trigger**: blank module pages / only bootstrap (such as locale) per route → content gate not passed.

| Symptom | Meaning |
|---|---|
| Application shell succeeds | Rendering + interceptor gates passed |
| Missing sidebar items/blank pages on click | Incomplete stub shape or permission codes |
| Identical, very few APIs per route | `v-if permission` did not pass |
| `routes.txt` much smaller than bundle | Complete it from the auth module |

```bash
grep -rhoaE '"/api[^"]*(permission|perm|role|menu|acl)[^"]*"' OUTDIR/js/*.js | sort -u | head -30
grep -rhoaE 'userRouteAuth|getResultTree|routeMap|routeLink|menuList|authList' OUTDIR/js/*.js | head -20
```

Typical chain: `role_permissions` (flat codes) + `permissions/all` (tree) → `getResultTree` → `userRouteAuth[CODE].url`.

```bash
python3 recon/extract_route_map.py recon/js recon/
python3 recon/build_perm_tree.py recon/js recon/ --config recon/config.json
```

Intermediate outputs: `route_map.json`, `userRouteAuth.json`, `permissions_tree.json`, `*_stub.json`, and `perm_codes_all.txt`.

Stub checks: outer `response_code` matches interceptor gate; flat codes align with tree; `routes` covers every link in `route_map`.

After updating `config.json`, **rerun Phase 3**. For large SPAs, adjust `waitUntil`, `routeTimeout`, and `perRouteMs` (reference sections A3/I).

---

## Phase 5 — Merge and Report

### Output Table

| File | Phase | Content |
|---|---|---|
| `js/`, `api_static.txt`, `routes.txt`, `chunkmap.txt` | 1 | Static bundles and paths |
| `param_candidates.json` | 1b | Static parameter-field candidates |
| `config.json` | 2 | Three gates + runtime configuration |
| `runtime_api.json` | 3a | Detailed depth recording (including WS/SSE) |
| `param_samples.json`, `scan_raw.json`, `api_detail.json` | 3b | Multiple samples, click logs, detail |
| `route_map.json`, etc. | 4 | Permission-tree intermediate files (if performed) |
| `params_merged.json` | 5 | Merged parameter fields + confidence |
| `api_merged.txt` | 5 | `METHOD /path [params] [static\|runtime\|both]` |
| `site_map.json` | 5 | Routes, APIs, params, features, limitations |
| **insert_assets** | 5 | Write all service and endpoint assets into the asset database |

### 5b — Parameter Merge

Diff `param_samples.json`; **there is no universal merge script**. Confidence rules: reference J7 (high/medium/low/pending trigger).

### 5c — Inference from Errors

Within authorization scope, send incomplete requests and inspect 400 errors (**parameter recon, not vulnerability testing**): `field 'x' is required`, enumeration errors, etc. Watch `data` wrappers, `variables`, and pre-encryption `bizData`.

The report must state runtimeMode, static/runtime API counts, parameter confidence, uncovered modules, and a `CHANGES.md` summary of deviations from reference scripts.

Suggested `site_map.json` structure:

```json
{
  "site": "https://example.com",
  "runtimeMode": "both",
  "appType": "vue-spa",
  "routeGuardStrategy": ["nav-neutralize", "L1-auth", "L2-patch", "forward"],
  "apisFromStatic": [],
  "apisFromRuntime": [],
  "apis": [],
  "params": [{ "method": "POST", "path": "/api/user/list", "transport": "json", "fields": [] }],
  "frontendRoutes": [],
  "routesVerifiedByClick": [],
  "featuresTriggered": [],
  "limitations": ""
}
```

For more fields and grep recipes, see [reference.md](reference.md).

---

## General Notes

- **Framework-independent**: same method for webpack/Vite/Angular lazy loading
- **Transports**: REST/JSON, GraphQL, WebSocket, SSE; gRPC-web is out of scope
- **SSR**: client fetches can be recorded; RSC/Server Actions cannot be fully enumerated
- **Blind spots**: JSVMP, WASM, strict HMAC/mTLS validation → static analysis + stated limitations
- **Parameter blind spots**: conditional dependencies, hidden params, WASM request assembly → "pending trigger" / "unreachable"
- **Static is the safety net**: endpoints remain discoverable statically when runtime is blocked

---

## Additional Resources

- Grep recipes, `config.json` templates, troubleshooting, hooks, parameter reverse engineering in section J, site_map template: **[reference.md](reference.md)**
- Reference-script paths: [Scripts and Gates](#scripts-and-gates) table
