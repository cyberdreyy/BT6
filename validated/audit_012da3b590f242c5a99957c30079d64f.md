### Title
Unauthenticated exposure of LOOP plugin pprof profiling data and metrics via `/plugins/:name/*` routes - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the base `api` router group with no `auth.Authenticate` wrapper, unlike every other sensitive route in `v2Routes` and unlike the analogous `metricRoutes(authv2)` pprof endpoints that are explicitly gated behind session/token authentication. Any unauthenticated network caller able to reach the node's web server can retrieve LOOP plugin Prometheus metrics and full Go pprof runtime profiles (heap, goroutine, profile, trace, cmdline, symbol) for any registered plugin name.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the base `api` group (`engine.Group("/", rateLimiter(...), sessions.Sessions(...))`, `router.go:78-85`) and calls `loopRoutes(app, api)` (`router.go:91`) alongside `debugRoutes`, `healthRoutes`, `sessionRoutes`, and `v2Routes`. Unlike `debugRoutes`, which wraps `/debug/vars` in `auth.Authenticate(...)` (`router.go:180-183`), and unlike `metricRoutes`, which is only ever invoked on `authv2` — the already-authenticated group (`router.go:446`) — `loopRoutes` attaches its handlers straight to `r` with zero auth middleware:

```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
``` [1](#0-0) 

`pluginPPROFHandler` (`core/web/loop_registry.go:150-166`) takes the caller-supplied `:name` and `*profile` path parameters, looks up the plugin in the registry, and proxies the request unauthenticated to the plugin's internal pprof HTTP server, returning the raw response body to the caller with `gc.Data(http.StatusOK, "text/plain", b)` (`loop_registry.go:214`). Likewise `pluginMetricHandler` proxies to the plugin's `/metrics` endpoint and returns the body directly (`loop_registry.go:96-128`). Neither handler nor the route registration performs any authentication or authorization check — there is no `auth.Authenticate`, no session cookie check, no API token check.

### Impact Explanation
This is unauthenticated information disclosure: any caller with network access to the node's API port can retrieve full Go runtime profiling data (heap dumps, goroutine stacks, CPU profiles, symbol tables) and Prometheus metrics for LOOP plugins without any credential. Heap/goroutine dumps and profiles can leak internal memory layout, in-flight data, configuration values, and other information useful for planning further attacks against the node, matching an unauthenticated info-disclosure impact class.

### Likelihood Explanation
No credential, role, or token is required — a plain unauthenticated HTTP GET to a known/guessable plugin name is sufficient. The attacker only needs network reachability to the node's web server, which the surrounding rate limiter and CORS/security middleware do not gate by identity. This is trivially repeatable and requires no special preconditions beyond at least one LOOP plugin being registered in `l.registry`.

### Recommendation
Wrap the `loopRoutes` group in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (or an equivalent admin/run-role check), consistent with how `metricRoutes` is only mounted under `authv2` and `/debug/vars` is protected in `debugRoutes`.

### Proof of Concept
Go handler-level integration test plan:
1. Build a test `chainlink.Application` with a `LoopRegistry` containing one registered plugin (e.g., name `"median"`, with `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` that answers `/debug/pprof/heap` and `/metrics`).
2. Call `web.NewRouter(app, nil)` to construct the full router as done in production.
3. Using `httptest.NewRecorder()` and `http.NewRequest("GET", "/plugins/median/debug/pprof/heap", nil)` with **no** `Authorization` header and **no** session cookie, dispatch the request through the engine.
4. Assert the response status is `200 OK` with pprof body content returned (reproducing the vulnerability) rather than `401`/`403`.
5. Repeat for `GET /plugins/median/metrics` with no auth headers, asserting `200 OK` with metrics text returned.
6. As a regression check after the fix, assert both requests return `401 Unauthorized` when no credentials are supplied, and `200 OK` when a valid session/token is supplied.

### Citations

**File:** core/web/router.go (L230-236)
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
