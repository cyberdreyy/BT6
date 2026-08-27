### Title
Unauthenticated exposure of LOOP plugin pprof debug endpoints via loopRoutes - ([File: core/web/router.go])

### Summary
`loopRoutes` in `core/web/router.go` registers `GET /plugins/:name/debug/pprof/*profile` and `POST /plugins/:name/debug/pprof/symbol` directly on the base router group `r` (the `api` group), without wrapping them in any `auth.Authenticate` middleware. Any unauthenticated network caller can reach `LoopRegistryServer.pluginPPROFHandler` and `pluginPPROFPOSTSymbolHandler`, which proxy the request to the LOOP plugin's internal pprof server and return its raw response.

### Finding Description
In `NewRouter`, the `api` group is created with only rate-limiting and session middleware (`core/web/router.go:78-85`), and `loopRoutes(app, api)` is called at `core/web/router.go:91` alongside `debugRoutes`, `healthRoutes`, `sessionRoutes`, `v2Routes`. Inside `loopRoutes` (`core/web/router.go:230-236`):
```go
r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
```
Neither route is wrapped with `auth.Authenticate(...)`, unlike the comparable node-self pprof routes: `debugRoutes` gates `/debug/vars` with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (`core/web/router.go:180-182`), and `metricRoutes(authv2)` for the node's own `/debug/pprof/*` is only mounted inside the authenticated `authv2` group (`core/web/router.go:446`). The plugin pprof routes have no equivalent guard.

`pluginPPROFHandler` (`core/web/loop_registry.go:150-166`) looks up the plugin by name from `l.registry`, builds an internal URL `http://<loopHostName>:<PrometheusPort>/debug/pprof/<profile>`, and forwards the request via `l.doRequest`, returning the plugin's raw pprof output (heap dump, goroutine stacks, etc.) directly to the caller with `gc.Data(http.StatusOK, ...)`. There is no session/token/role check anywhere in this call chain, so any client that can reach the node's HTTP port can hit these endpoints without credentials.

### Impact Explanation
pprof heap/goroutine/allocs dumps can contain sensitive in-memory data (potentially including secrets, keys, or internal state held by LOOP plugins such as median/OCR plugins). Unauthenticated disclosure of this data corresponds to Chainlink's "information disclosure / secret exposure" bounty impact class, and could aid further attacks (e.g., extracting private key material or internal configuration from memory).

### Likelihood Explanation
No preconditions are required — the attacker needs only network access to the node's HTTP listener and a plugin name (obtainable via the also-unauthenticated `GET /discovery` endpoint at `core/web/router.go:232`, which lists registered plugins). The attack is trivially repeatable with a single HTTP GET/POST request.

### Recommendation
Wrap the `loopRoutes` pprof endpoints (and ideally the `/plugins/:name/metrics` and `/discovery` endpoints) with the same `auth.Authenticate` middleware used elsewhere (e.g., `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`), or move them under the authenticated `authv2` group similar to how `metricRoutes(authv2)` gates the node's own pprof endpoints.

### Proof of Concept
1. Build a `*gin.Engine` via `NewRouter` (or a minimal router calling `loopRoutes`) with an `app` whose `GetLoopRegistry()` returns a registry containing a fake plugin "median" pointing `EnvCfg.PrometheusPort` at a local `httptest.Server` that serves `net/http/pprof` handlers.
2. Start `httptest.NewServer(router)`.
3. Send `GET /plugins/median/debug/pprof/heap` with no `Authorization` header and no session cookie.
4. Assert response status is `200 OK` (not `401 Unauthorized`), and body contains pprof heap profile bytes.
5. Repeat with `POST /plugins/median/debug/pprof/symbol` to confirm the same unauthenticated access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L150-166)
```go
func (l *LoopRegistryServer) pluginPPROFHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/"+gc.Param("profile"), l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	l.logger.Infow("Forwarding plugin pprof request", "plugin", pluginName, "url", pluginURL)
	l.doRequest(gc, "GET", pluginURL, nil, timeout, pluginName)
}
```
