### Title
Unauthenticated disclosure of raw plugin Prometheus metrics via `/plugins/:name/metrics` - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/metrics` route is registered on the base `api` router group with no authentication middleware, allowing any unauthenticated client to trigger `LoopRegistryServer.pluginMetricHandler`, which proxies the raw `/metrics` response from the internal LOOP plugin process verbatim to the caller with zero filtering or redaction.

### Finding Description
In `core/web/router.go`, `loopRoutes(app, api)` is called directly on the top-level `api` group at line 91, alongside `debugRoutes`, `healthRoutes`, `sessionRoutes`, and `v2Routes`. Unlike `debugRoutes` (which wraps its group with `auth.Authenticate(...)`, line 181) or `authv2` (wrapped with `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)`, lines 245-248), `loopRoutes` registers `r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)` (`core/web/router.go:233`) with no auth middleware at all — only the generic rate limiter and session-cookie middleware applied to the whole `api` group (`core/web/router.go:78-85`), neither of which reject unauthenticated requests.

In `pluginMetricHandler` (`core/web/loop_registry.go:96-128`), the handler:
1. Looks up the plugin by name from the registry (`l.registry.Get(pluginName)`).
2. Issues an HTTP GET to the internal LOOP plugin's own `/metrics` endpoint (`pluginURL := fmt.Sprintf("http://%s:%d/metrics", ...)`).
3. Reads the full response body `b` via `io.ReadAll(res.Body)`.
4. Writes `b` back to the client unmodified via `gc.Data(http.StatusOK, "text/plain", b)` — no scrubbing, redaction, or content inspection of the proxied body occurs anywhere in this path.

Only the plugin *name* echoed in error messages is HTML-escaped (`html.EscapeString(pluginName)`); the actual metrics payload from the plugin is never sanitized. If a LOOP plugin's metrics exporter includes sensitive values in labels or metric text (e.g., key IDs, addresses, bridge names, or any other identifying data an integrator's plugin might emit), that data is returned to any caller who knows (or enumerates) a registered plugin name — without any credential.

### Impact Explanation
This is an unauthenticated information-disclosure path: any network client that can reach the node's web server can pull the complete internal Prometheus text exposition for any registered plugin, bypassing the authentication that protects every other operator-facing metrics/debug surface (`/debug/vars` requires session auth; `/debug/pprof/*` under `authv2` requires token/session auth). Depending on what labels/values a given LOOP plugin chooses to emit, this can leak key IDs, addresses, bridge names, or other operationally sensitive metadata, aligning with a "sensitive information disclosure" bounty class rather than direct fund loss.

### Likelihood Explanation
No credentials, session, or role are required — a plain unauthenticated `GET /plugins/<name>/metrics` request is sufficient, and plugin names can be discovered via the equally unauthenticated `GET /discovery` endpoint (`discoveryHandler`, `core/web/loop_registry.go:53-81`), which lists all registered plugin names in `LabelMetaPluginName`. This makes the attack fully repeatable and requires no special preconditions beyond network reachability to the node's API port and at least one registered LOOP plugin.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/metrics` and `/discovery` routes) with the same `auth.Authenticate(...)` middleware used for `authv2`/`debugRoutes`, and/or apply role checks (e.g., `auth.RequiresAdminRole`) consistent with other metrics/debug endpoints. Additionally, consider that the proxied plugin metrics body itself is not something the core node controls — if this endpoint must remain accessible for legitimate external Prometheus scraping, isolate it on a separate, non-publicly-routable listener rather than sharing the authenticated API's `api` gin group.

### Proof of Concept
1. In a `core/web` handler-level test (extending `loop_registry_internal_test.go` patterns), stand up a fake `LoopRegistryServer` with a stub `promClient` (`http.Client` pointed at an `httptest.Server`) whose `/metrics` handler returns a body containing a fake sensitive string, e.g. `test_metric{key_id="0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"} 1`.
2. Register a fake plugin in `l.registry` with a known name (e.g., `"medianpoc"`).
3. Build a `gin.Context` for `GET /plugins/medianpoc/metrics` with no `Authorization` header and no session cookie, and call `pluginMetricHandler(gc)` directly (or drive it through `NewRouter` with an unauthenticated `httptest` client to also prove the route bypasses `NewRouter`'s auth groups).
4. Assert HTTP 200 and that `gc.Writer`/response body (`w.Body.String()`) contains the exact unredacted string `key_id="0xdeadbeef..."`, proving the sensitive value passes through without redaction to an unauthenticated caller.