### Title
Unauthenticated LOOP-plugin pprof proxy allows profiling/debug data exposure and DoS bypassing the authenticated `/v2/debug/pprof` boundary - (File: core/web/router.go)

### Summary
`loopRoutes` registers `/plugins/:name/debug/pprof/*profile` and the corresponding `POST /plugins/:name/debug/pprof/symbol` directly on the top-level `api` router group, which only applies rate-limiting and session middleware — no authentication is required. This is in stark contrast to `metricRoutes(authv2)`, which exposes equivalent `net/http/pprof` functionality only behind `auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)`. Any unauthenticated network client can therefore pull heap/goroutine/cmdline/trace dumps and CPU profiles from any registered LOOP plugin process by guessing/enumerating `:name`.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the `api` group with only rate limiting and cookie session middleware attached (no `auth.Authenticate`): [1](#0-0) 

`loopRoutes` is called with this unauthenticated `api` group and registers the plugin debug/pprof proxy endpoints without any additional auth middleware: [2](#0-1) 

By contrast, the equivalent native-process pprof endpoints are deliberately placed behind the authenticated `/v2` group via `metricRoutes(authv2)`: [3](#0-2) 

The handler `pluginPPROFHandler` in `core/web/loop_registry.go` takes the unauthenticated `:name` and `*profile` path parameters, looks up the plugin in the registry, and blindly proxies the request to the plugin's internal `debug/pprof` endpoint, returning the raw response body to the caller: [4](#0-3) 

`pluginPPROFPOSTSymbolHandler` similarly forwards attacker-supplied POST bodies to the plugin's `/debug/pprof/symbol` endpoint with no auth check: [5](#0-4) 

Because these routes are mounted on `api` (the engine root group) rather than under an authenticated group like `authv2`, an unauthenticated attacker who knows or brute-forces a valid LOOP plugin name (e.g. via `/discovery`, which is also unauthenticated) can retrieve heap dumps, goroutine stacks, and CPU/execution traces of the plugin process, or trigger long-running `profile`/`trace` calls (`seconds` query param) against the plugin, causing resource exhaustion — all without any credentials, while the functionally equivalent native pprof surface is properly gated behind session/token authentication.

### Impact Explanation
This is an authentication/authorization boundary bypass for a debug/profiling capability: an unauthenticated attacker gains the same capability that requires authentication for the node's own pprof endpoints (heap/goroutine memory inspection, CPU/execution tracing) but scoped to LOOP plugin processes. Heap and goroutine dumps can leak sensitive in-memory data (e.g., configuration, key material transient state) held by plugin processes, and repeated `profile`/`trace` calls with attacker-controlled `seconds` values can be used for resource-exhaustion/DoS against plugin processes. This matches the "authentication bypass" / "sensitive data disclosure" bounty impact class.

### Likelihood Explanation
No credentials or preconditions are required beyond network reachability to the node's HTTP API and the existence of at least one registered LOOP plugin (which can be discovered via the also-unauthenticated `/discovery` and `/plugins/:name/metrics` endpoints). The request is a simple `GET /plugins/<name>/debug/pprof/heap` (or `/profile`, `/trace`, `/goroutine`, etc.), fully repeatable and trivially scriptable.

### Recommendation
Move the plugin debug/pprof routes (`/plugins/:name/debug/pprof/*profile` and `POST /plugins/:name/debug/pprof/symbol`) into an authenticated route group (e.g., register them the same way `metricRoutes(authv2)` is gated, requiring `auth.Authenticate` with `AuthenticateByToken`/`AuthenticateBySession`, and consider requiring an admin/edit role given the sensitivity of profiling data), so the auth boundary is uniformly enforced regardless of whether the caller targets the node's own pprof endpoints or a LOOP plugin's proxied pprof endpoints.

### Proof of Concept
Go handler-level integration test outline (using `httptest` + a mock plugin server, similar to existing router tests in `core/web`):
1. Start a mock HTTP server exposing `/debug/pprof/heap` to emulate a LOOP plugin.
2. Register the mock plugin in the app's `LoopRegistry` (via `app.GetLoopRegistry()`), pointing `EnvCfg.PrometheusPort` at the mock server's port.
3. Build the router via `NewRouter(app, nil)` as done in existing `core/web` router tests.
4. Send `GET /v2/debug/pprof/heap` with no auth header/cookie — assert `401 Unauthorized` (enforced by `auth.Authenticate` on `authv2`).
5. Send `GET /plugins/mock/debug/pprof/heap` with no auth header/cookie — assert `200 OK` and that the response body matches the mock server's pprof payload, demonstrating the unauthenticated bypass.
6. Optionally repeat with `GET /plugins/mock/debug/pprof/profile?seconds=30` to show the endpoint accepts attacker-controlled long-running profile requests without authentication.

### Citations

**File:** core/web/router.go (L78-91)
```go
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)

	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
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

**File:** core/web/router.go (L444-447)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
	}
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

**File:** core/web/loop_registry.go (L168-188)
```go
func (l *LoopRegistryServer) pluginPPROFPOSTSymbolHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/symbol", l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	body, err := io.ReadAll(gc.Request.Body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error reading plugin pprof request body: %s", err))
		return
	}
	l.doRequest(gc, "POST", pluginURL, bytes.NewReader(body), timeout, pluginName)
}
```
