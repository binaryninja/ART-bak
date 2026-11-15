# api-recon — Reference Manual

Grep recipes, `config.json` templates, and troubleshooting. Run all grep commands against `js/`. For single-line bundles, optionally run `js-beautify` or `sed 's/}/}\n/g'` first; raw grep with context windows usually suffices.

## Script Notes

All files in `scripts/` are **reference templates** and must be adapted to the target site before execution. Typical changes:

| Script | Common Adjustments |
|---|---|
| `harvest_static.py` | Endpoint regex, webpack/Vite manifest parsing, microfrontend publicPath, retries/concurrency |
| `runtime_harvest.js` | Neutralize field names/success values, stub matching/body structure, routes source, WS recording, `waitUntil`/`routeTimeout`/`proxy` |
| `preload.js` | `loginPathRe`, L1 stubs, `neutralize.fields`, `apiPattern`, whether L3 is enabled, `recordDetail`, `observe.*`, `neutralizeVueRouter` |
| `spider_mpa.py` | `--exclude` destructive links, cookie, depth/max, same-domain filtering |
| `extract_route_map.py` | `routeMap` / `routeLink` regex, KEY naming patterns |
| `build_perm_tree.py` | `userRouteAuth` parsing, `ROOTS`/`PREFIX_PARENT` hierarchy heuristics, outer stub field names |
| `config.json` | Central entry point for all site-specific parameters above |

Place adapted files in the task working directory (such as `recon/`) and state specific changes from reference scripts in the report.

---

## A. Reverse Engineering the Three Gates

### A1. Rendering Gate — "How Is Logged-In State Determined?"

```bash
grep -rhoaE '.{0,40}(isLogin|isAuthenticated|loggedIn|hasLogin|requireAuth)\b.{0,80}' js | head
grep -rhoaE 'function (getUser|getToken|getAuth)[0-9]?\([^)]*\)\{.{0,200}' js | head
grep -rhoaE '(localStorage|sessionStorage)\.getItem\("[^"]+"\)' js | sort -u
grep -rhoaE '(Cookies?|cookie)\.(get|load)\("[^"]+"\)' js | sort -u
grep -rhoaE '\batob\(|JSON\.parse\(|jwt|decode' js | head
```

Find the chain `isLogin = f(getUser())` → `getUser = decode(storage.read(KEY))`; identify the **storage key**, **container** (Cookie vs localStorage), and **encoding**:

| Encoding | Mocking Method in config |
|---|---|
| Plain text / `"1"` / token | `"value": "anything-truthy"` |
| `JSON.parse(x)` | `"value": "json:{\"id\":1,\"username\":\"admin\"}"` |
| `JSON.parse(atob(x))` | `"value": "b64json:{\"id\":1,\"username\":\"admin\"}"` |
| JWT | Unsigned/`alg:none` JWT, or signed using a key embedded in the bundle |
| Encryption (SM2/AES/RSA) | Find hardcoded keys; forge when the rendering gate only needs a decodable blob; otherwise fall back to static analysis |

→ Write into `cookies` / `localStorage`.

### A2. Interceptor Gate — "What Triggers a Redirect to /login?"

The Bash Unicode escapes below preserve matching of the original Chinese login/error messages; they are compatibility inputs, not untranslated instructions.

```bash
grep -rhoaE '.{0,60}(interceptors\.response|axios|request\.use).{0,120}' js | head
grep -rhoaE '.{0,40}(response_code|errcode|errno|\bcode\b|\bret\b|\bstatus\b)\s*[=!]==?\s*[\-0-9]{1,4}.{0,60}' js | head -20
grep -rhoaE $'.{0,40}(\u672a\u767b\u5f55|\u8bf7\u91cd\u65b0\u767b\u5f55|\u767b\u5f55\u5df2\u8fc7\u671f|unauthorized|\u767b\u5f55\u5931\u6548|\u6388\u6743|token.{0,10}invalid).{0,40}' js | head
grep -rhoaE '.{0,30}(location\.href|router\.(push|replace)|navigate)\([^)]*login[^)]*\)' js | head
```

Determine **field names**, **success values** (usually `0` or `200`), and **failure values that trigger redirects**. Verify with a junk session:

```bash
curl -sk -X POST -H 'Cookie: <fakekey>=junk' https://target/api/<protected> -d '{}' -H 'Content-Type: application/json'
```

→ Write `neutralize.fields` + `neutralize.success`.

### A3. Content Gate — "Where Do Menus/Permissions Come From?"

```bash
grep -rhoaE '"/api[^"]*(permission|perm|role|menu|acl|resource|nav)[^"]*"' js | sort -u
grep -rhoaE '.{0,30}(menus|permissions|menuList|routeList|authList|role_permissions)\b.{0,120}' js | head
grep -rhoaE 'userRouteAuth|getResultTree|routeMap|routeLink|hasPermission|checkAuth' js | head
grep -rhoaE '([A-Z_][A-Z0-9_]*):\{name:"[^"]*",link:"/[^"]+"\}' js | head
```

**Two data layers** (common enterprise backends):

| API | Typical Payload | Consumer |
|---|---|---|
| `.../role_permissions` | `{ permissions: string[], role_type }` | Route guards, button-level ACL |
| `.../permissions/all` | `tree[{ code, position, children }]` | Sidebar menu rendering |
| `userRouteAuth` in bundle | `{ CODE: { url, name? } }` | code → frontend path |
| `routeMap` in bundle | `{ KEY: { name, link } }` | Alias resolution (webpack `o.DASHBOARD`) |

Read consumer code to verify how `getResultTree(tree, permissions)` filters and which fields `v-if` / `hasAuth(code)` checks.

**Manual forging** (small sites): build a permissive payload → `stubs`.

**Full permission-tree reconstruction** (large sites with blank sidebar/submodules): see **section I**.

---

## B. config.json Template

```json
{
  "baseUrl": "https://target/",
  "runtimeMode": "both",
  "chromium": "/usr/bin/chromium",

  "cookies": [
    { "name": "auth", "value": "b64json:{\"id\":1,\"username\":\"admin\",\"role\":\"admin\",\"func\":{},\"permissions\":[\"*\"]}" }
  ],
  "localStorage": { "token": "faketoken", "isLogin": "1" },

  "neutralize": {
    "fields": ["response_code", "code", "errno", "ret", "status"],
    "success": 0,
    "flags": { "success": true, "message": "ok" }
  },
  "forward": true,
  "loginUrlPattern": "/login",
  "apiPattern": "/api/|/rest/|/graphql",

  "mockTier": "L1+L2",
  "recordDetail": true,
  "observe": {
    "storageReads": false,
    "cookieReads": false,
    "xhrHeaders": true
  },
  "neutralizeVueRouter": true,
  "stubs": [
    {
      "match": "permissions/all|/menu|role_permissions",
      "body": {
        "response_code": 0, "code": 0,
        "data": {
          "permissions": ["*"],
          "menus": [
            { "name": "dashboard", "path": "/dashboard", "show": true, "children": [] },
            { "name": "alert", "path": "/alert", "show": true, "children": [] }
          ]
        }
      }
    }
  ],

  "explore": {
    "clickTabs": true,
    "clickTables": true,
    "pushStateFallback": true,
    "maxMenuItems": 50
  },

  "routes": ["/dashboard", "/alert", "/asset", "/device", "/report", "/config", "/system"],
  "waitMs": 1500, "perRouteMs": 900, "headless": true,
  "waitUntil": "domcontentloaded",
  "routeTimeout": 12000,
  "proxy": "",

  "captureResponses": true, "recordWs": true, "respMax": 600
}
```

Field notes:
- `runtimeMode`：`depth`（Puppeteer）、`coverage`（browser MCP）、`both`
- `cookies[].value` prefixes: `b64json:` → base64(JSON); `json:` → raw JSON; no prefix → literal
- `forward: true` forwards real requests and rewrites code fields; `false` uses fully offline stubs
- `mockTier`: layers enabled in coverage-mode preload, such as `L1+L2`, `L1+L2+L3`
- `routes` comes from `routes.txt`; after menus are forged, the harness automatically appends `<a href>`
- `captureResponses` / `recordWs` apply only to depth mode
- `waitUntil`: use `domcontentloaded` for large SPAs to avoid `networkidle2` hanging
- `routeTimeout`: per-route `page.goto` timeout in milliseconds
- `proxy`: Puppeteer `--proxy-server`; alternatively set `HTTP_PROXY` / `HTTPS_PROXY`

### B1. Dual-Stub Template (role_permissions + permissions/all)

```json
"stubs": [
  {
    "match": "role_permissions",
    "body": {
      "response_code": 0,
      "data": {
        "permissions": ["MONITOR", "MONITOR_ALERT", "THREAT", "ASSETS_RISK"],
        "role_type": "SUPER_ADMIN"
      }
    }
  },
  {
    "match": "permissions/all",
    "body": {
      "response_code": 0,
      "data": [
        {
          "code": "MONITOR",
          "position": 1,
          "children": [
            { "code": "MONITOR_ALERT", "position": 1, "children": [] }
          ]
        }
      ]
    }
  }
]
```

Outer field names (`response_code` / `code` / `data`) must match the A2 interceptor gate; `permissions` must cover all leaf codes in the tree.

---

## C. coverage Mode: preload Configuration

Edit the `CONFIG` object at the top of `scripts/preload.js`, or replace it before CDP injection:

```javascript
const CONFIG = {
  loginPathRe: /\/(login|signin)(\/|$|\?)/i,
  mockTier: 'L1+L2',
  forward: true,
  recordDetail: true,
  extractUrlsFromResponse: true,
  neutralizeVueRouter: true,
  observe: { storageReads: false, cookieReads: false, xhrHeaders: true },
  neutralize: { fields: ['response_code', 'code'], success: 0 },
  stubs: [ /* Same as config.json stubs */ ],
  apiPattern: /\/(api|apis|v\d+|dev|internal|graphql)\//i,
};
```

Verify: `window.__API_RECON_PRELOAD__ === true` and pathname stays stable.

Export recordings:

```javascript
JSON.stringify({
  apis: [...window.__API_RECON_LOG__],
  detail: window.__API_RECON_DETAIL__,
  routes: [...(window.__API_RECON_ROUTES__ || [])],
  observe: window.__API_RECON_OBSERVE__,
}, null, 2)
```

---

## D. preload / runtime Hook Capabilities

Built-in browser hooks and coverage in preload (coverage) and runtime_harvest (depth):

| Hook Capability | Value for API Discovery | Coverage |
|---|---|---|
| Hook fetch / XHR.open | Record request URL/method | ✅ `recordDetail` + `__API_RECON_LOG__` |
| Hook XHR.setRequestHeader | Discover Authorization and other headers | ✅ `observe.xhrHeaders` |
| Hook localStorage/cookie reads | Confirm session key names | ⚠️ Optional `observe.storageReads/cookieReads` |
| Get Vue routes | Complete frontendRoutes | ✅ `__API_RECON_ROUTES__` (loaded routes) |
| Neutralize Vue route guards / block login redirects | Render modules to trigger APIs | ✅ `neutralizeVueRouter` + native-navigation neutralization |
| Get React routes | Supplement routes | ⚠️ Static + clicks; no dedicated hook |
| Block page navigation (login paths) | Stay on page for analysis | ⚠️ Block login paths only, not business navigation |
| Hook encryption libraries (CryptoJS/SM, etc.) | Encrypted parameters → plaintext API body | ❌ Manually hook encryption-function inputs; record findings in config |
| Anti-debugging bypass | Otherwise runtime cannot record APIs | ❌ Requires manual handling; static analysis remains available |

---

## E. Endpoint Extraction Regex (When Static Results Are Sparse)

Relax `extract_endpoints` in `harvest_static.py`, or do it manually:

```bash
grep -rhoaE '"/[a-z][A-Za-z0-9_/\-]{3,}"' js | sort -u
grep -rhoaE '/api/[a-zA-Z0-9_./-]+' js | sort -u
```

---

## F. Troubleshooting

| Symptom | Cause → Action |
|---|---|
| Few static APIs | Endpoint dialect mismatch → relax regex (section D) |
| Chunk count ≪ manifest | CSS-only or undeployed chunks; 404s have been retried |
| Runtime still shows login page | Rendering gate incorrect → recheck A1: key, container, encoding, domain |
| Shell opens but modules are blank | Content gate → forge menus (A3); `routes` paths may be incorrect |
| Only bootstrap/locale per route | Incomplete permission codes → reconstruct tree in section I; check dual `role_permissions` + `permissions/all` stubs |
| Sidebar items exist but subpages are blank | Missing intermediate tree nodes, or codes differ from `userRouteAuth` |
| Every API redirects to login | Interceptor gate → verify `neutralize`; nested fields require extended walk logic |
| Zero WS frames | Subscription may require interaction; increase `perRouteMs` |
| Empty response bodies | Real responses exist only with `forward: true` |
| Missing Chromium | Install chromium or set `config.chromium` / `CHROMIUM` |
| Still returns to login despite many mocks | Hook too late or missing `location.href` setter → document-start + preload |
| All lists empty | L3 empty arrays are normal; continue clicking tabs/settings/details |
| Redux actions mistaken for routes | Filter internal paths containing get/set/change/clear/toggle/upload |
| Vue still redirects to login | Preload not at document-start → change injection timing; or manually clear guards if `neutralizeVueRouter: false` |
| Response URLs missing from log | Enable `extractUrlsFromResponse`; or manually extract from `__API_RECON_DETAIL__` |
| Unknown Authorization header name | Enable `observe.xhrHeaders` or inspect request headers in DevTools |
| Runtime extremely slow / times out | Use `waitUntil: domcontentloaded`; lower `routeTimeout`; avoid `networkidle2` |
| Proxy connection fails | Check `proxy` / environment variables; use matching proxy ports in Puppeteer and curl |

---

## G. Hardened Targets

When the server validates sessions step by step (unforgeable signed cookies, server-rendered menus that cannot be stubbed), runtime gets stuck at the shell. Expected behavior:

- **Static analysis is sufficient for endpoint enumeration**: module paths are in the code
- If authorization permits, run the same harness with a **real session**: `forward: true`, no neutralize needed, capturing real methods/params/responses

---

## H. Per-Task Checklist

1. Confirm authorization scope
2. **Read** `scripts/harvest_static.py` → adapt to target → run → review `api_static.txt`, `routes.txt`
3. **Phase 1b**: expanded path-anchor windows + binding layer → `param_candidates.json` (section J)
4. Reverse engineer A1/A2/A3 → write site-specific `config.json`
5. **Read and adapt** `runtime_harvest.js` / `preload.js` before execution
6. `runtimeMode=depth`: `npm install` → run adapted harvest script
7. `runtimeMode=coverage/both`: inject adapted preload at document-start → browser MCP dynamic enumeration + **parameter-trigger matrix**
8. Modules not rendering → **section I permission-tree reconstruction** → patch stubs → rerun
9. Multi-sample parameter diffs + error inference → `params_merged.json`
10. Merge → `site_map.json` + `api_merged.txt`; honestly state coverage, gaps, and script changes

---

## I. Permission Tree Reconstruction (Phase 4 in Depth)

Use when forging a simple `menus: [{ path, show: true }]` does not work and submodules still do not mount.

### I1. Locate the Auth Module

```bash
grep -l 'userRouteAuth' js/*.js
grep -l 'routeMap\|routeLink' js/*.js
grep -rhoaE 'getResultTree|role_permissions|permissions/all' js | head
```

Record **permission API paths**, **response field names**, and **consumer chunk filenames**.

### I2. Extract routeMap

```bash
python3 scripts/extract_route_map.py recon/js recon/
# Produces recon/route_map.json
```

If `[!] no routeMap pattern found`: relax regex in `extract_route_map.py`, or grep manually:

```bash
grep -rhoaE '([A-Z_][A-Z0-9_]*):\{name:"[^"]*",link:"/[^"]+"\}' js | head -20
```

### I3. Build Permission Tree + Stubs

```bash
python3 scripts/build_perm_tree.py recon/js recon/ --config recon/config.json
```

Script logic:
1. Parse `userRouteAuth={MONITOR:{url:...},...}` (including webpack aliases such as `He=o.DASHBOARD`)
2. Resolve aliases → actual paths using `route_map.json`
3. Infer parents from code prefixes (`MONITOR_ALERT` → `MONITOR`)
4. Output `permissions_tree.json`, `permissions_all_stub.json`, and `role_permissions_stub.json`
5. With `--config`, automatically write `config.json` stubs and expand `routes`

**Adapt to the target** (at the top of the script):
- `DEFAULT_ROOTS`: list of top-level module codes
- `DEFAULT_PREFIX_PARENT`: `PREFIX_` → parent mapping
- `DEFAULT_EXTRA_PARENT`: orphan nodes without prefix relationships

### I4. Verify Stub Consistency

```bash
# Permission count should approximately match userRouteAuth entry count
wc -l recon/perm_codes_all.txt
# routes should cover every link in route_map
python3 -c "import json; m=json.load(open('recon/route_map.json')); r=set(json.load(open('recon/config.json'))['routes']); print('missing', [v['link'] for v in m.values() if v['link'] not in r])"
```

### I5. Rerun Runtime and Compare

```bash
node recon/runtime_harvest.js recon/config.json
# Compare runtime_api.json counts before/after forging; check whether /attack, /asset, etc. produce module APIs
```

| Before Forging | After Forging (Success) |
|---|---|
| Same 3–5 bootstrap requests per route | Different routes trigger different module APIs |
| Only `/api/locale/language` | `/api/web/...` module endpoints appear |
| Single-digit route count in `routes.txt` | 80–110+ `routes` from route_map |

### I6. If It Still Fails

- **coverage mode**: click sidebar + tabs; permission gating may request data only after interaction
- **Stub fields**: compare nesting in real APIs (curl + real session) and stubs
- **Extra guards**: grep button-level checks such as `hasPermission|checkRole|func.` and extend `role_permissions.permissions`
- **Static fallback**: module API paths remain in `api_static.txt`; runtime adds METHOD/body only; keep `param_candidates.json` + recorded samples

---

## J. Parameter Reverse Engineering (Phase 1b / 5b / 5c)

**A methodology, not a universal script.** Use regex for paths; use expanded anchor windows + UI binding chains + multi-sample diffs + error inference for parameters.

### J1. Expanded Anchor Windows — Find Request Objects from Paths

```bash
# Anchor on paths already known from Phase 1
grep -n '"/api/user/list"' js/*.js
grep -rhoaE '.{0,120}("/api[^"]+").{0,200}' js | head
grep -rhoaE '(params|data|body|payload)\s*:\s*\{' js | head
grep -rhoaE '(get|post|put|delete|patch)\([^,]+,\s*\{' js | head
```

### J2. Wrapper Layers and Transport Forms

```bash
# axios / unified request wrapper
grep -rhoaE '(axios|request)\.(get|post|put|delete|patch)\(' js | head
grep -rhoaE 'interceptors\.(request|response)' js | head

# GraphQL
grep -rhoaE '(query|mutation)\s+\w+|gql`|graphql\(' js | head
grep -rhoaE '\$[a-zA-Z_]+\s*:\s*(Int|String|Boolean|\[)' js | head

# FormData / multipart
grep -rhoaE 'FormData|\.append\(' js | head

# Path parameters
grep -rhoaE 'path:\s*"/[^"]*:[^"]+"' js | head
grep -rhoaE 'useParams|route\.params|\$route\.params' js | head
```

### J3. Validation Gate — Required / Format / Enumeration

```bash
grep -rhoaE '(required|message|pattern|enum|validator)\s*:' js | head
grep -rhoaE 'yup\.|zod\.|async-validator|Form\.Item|a-form-item|el-form-item' js | head
grep -rhoaE 'rules\s*:\s*\[|name:\s*["\'][a-zA-Z_]+["\']' js | head
grep -rhoaE 'label.*value|options\s*:\s*\[' js | head
```

### J4. Binding Layer — Form → API

```bash
grep -rhoaE 'onFinish|handleSubmit|getFieldsValue|validateFields' js | head
grep -rhoaE '(pick|omit|transform|dayjs|moment)\(' js | head
```

Runtime supplement: DevTools → Network → request → **Initiator** (call stack); trace upward from `fetch`/`send` to request-construction functions.

### J5. Encrypted Parameters

```bash
grep -rhoaE 'encrypt|decrypt|sign|CryptoJS|sm2|sm3|sm4|RSA|AES' js | head
```

**Do not guess fields from ciphertext**: hook encryption-function **inputs** and record plaintext payloads before encryption; write findings into `config.json` / `param_candidates.json`.

### J6. Parameter-Trigger Matrix (Required in Phase 3)

Record each operation once per module and diff request body/query:

| Operation | Focus |
|---|---|
| Initial list page | Pagination defaults |
| Search | keyword, filters |
| Advanced filters | Optional fields |
| Create/edit | Complete entity |
| Bulk/export | `ids[]`, `exportType` |
| Sort/paginate | `sortField`, `order` |

Produce `param_samples.json`: `[{ "path", "method", "action": "search", "body", "query", "headers" }]`

### J7. Confidence Rules

| Confidence | Condition |
|---|---|
| **High** | Static callsite + ≥2 consistent runtime samples |
| **Medium** | Static only, or only 1 runtime sample |
| **Low** | Inferred from responses/errors, not independently verified |
| **Pending trigger** | Fields known statically but UI/permissions have not reached them |

### J8. Scenario Quick Reference

| Scenario | Sequence |
|---|---|
| REST list page | J1 request objects → J6 four diffs → J3 rules |
| Create/edit form | J3 Form name → J4 submit chain → runtime submission + intentionally empty fields to inspect 400 |
| GraphQL | J2 variables declarations → record variables for each runtime operation |
| Encrypted body | J5 hook inputs → pre-encryption fields are actual params |

### J9. Mapping to api-recon Phases

| api-recon | Parameter Recon |
|---|---|
| Phase 1 static | J1 expanded anchor windows |
| Phase 2 A2 interceptor | Globally injected fields (tenantId, sign) |
| Phase 3 runtime | J6 trigger matrix + `param_samples.json` |
| Phase 4 permission tree | Module forms differ → sufficient permissions needed to trigger every field |
| Phase 5 merge | `params_merged.json` + confidence; never infer required fields from one sample |

### J10. Troubleshooting

| Symptom | Action |
|---|---|
| Static field name never appears at runtime | Mark "pending trigger"; complete permission tree / click advanced filters / exercise each dependent select option |
| Different body shapes for the same path | Normal: record separately by `action`, do not force schemas together |
| Stub response is fake but params are needed | Inspect **outbound request** bodies/headers, not inferred stub-response fields |
| 400 reports a nested field | Watch outer `data`/`bizData`/`variables` wrappers |
| GraphQL shows only operation names | Expand `variables` JSON; locate `$var: Type` statically |

---
