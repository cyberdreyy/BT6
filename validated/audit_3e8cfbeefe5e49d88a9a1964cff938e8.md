Confirmed: `loopRoutes` registers `/plugins/:name/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the `api` group with no auth middleware applied, unlike every other sensitive endpoint (`v2Routes`, `debugRoutes`, `sessionRoutes`) which wrap their groups with `auth.Authenticate(...)`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unauthenticated forwarding of arbitrary pprof symbol lookup requests to plugin debug endpoints - (File: core/web/loop_registry.go)

### Summary
The `/plugins/:name/debug/pprof/symbol` route (and sibling `/plugins/:name/debug/pprof/*profile`, `/plugins/:name/metrics`) is registered in `loopRoutes` directly on the base `api` router group without any authentication middleware, unlike every other route group in `router.go` (`v2Routes`, `debugRoutes`, `sessionRoutes`) which explicitly wraps handlers with `auth.Authenticate(...)`. This allows an unauthenticated attacker to POST symbol-address lookups to `pluginPPROFPOSTSymbolHandler`, which forwards the raw request body to the plugin's internal `/debug/pprof/symbol` endpoint and returns the plugin's raw response, potentially disclosing internal Go function/symbol names.

### Finding Description
`loopRoutes` mounts these handlers on `r` (the `api` group from `NewRouter`), which only carries rate-limiting and session-cookie middleware — no `auth.Authenticate` wrapper is applied, in contrast to `v2Routes`/`debugRoutes`/`sessionRoutes` which explicitly gate their groups. `pluginPPROFPOSTSymbolHandler` reads the request body unconditionally, builds a URL to the plugin's loopp process (`http://<loopHostName>:<PrometheusPort>/debug/pprof/symbol`), forwards the client body via `doRequest`, and writes the plugin's raw response straight back to the caller with `gc.Data(http.StatusOK, ...)`. There is no session/token check, no role check, and no plugin-name allowlist against the caller's identity — only whether the named plugin exists in the registry. Any unauthenticated caller who knows (or brute-forces) a registered plugin name can retrieve symbol information revealing internal function names/addresses.

### Impact Explanation
This maps to an information-disclosure impact: exposure of internal Go runtime symbol/function names for loopp plugin processes to unauthenticated callers, which aids attackers in reconnaissance/exploit-development against the node's internal plugin architecture. It does not directly yield credential theft or fund movement, so severity is informational/low-to-medium (internal architecture disclosure) rather than critical, but it is a genuine authentication-bypass on a debug endpoint that the rest of the router explicitly protects.

### Likelihood Explanation
Very high feasibility: no credentials, tokens, or session cookies are required at all; the attacker only needs network access to the node's HTTP API and a valid plugin name (which can be discovered via the also-unauthenticated `/discovery` and `/plugins/:name/metrics` routes registered in the same `loopRoutes` function). The request is a single unauthenticated POST, fully repeatable.

### Recommendation
Wrap `loopRoutes`' router group with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware (and an appropriate role requirement, e.g., admin/run role) used elsewhere in `router.go`, consistent with how `debugRoutes` and `metricRoutes` under `authv2` are protected.

### Proof of Concept
Handler-level integration test using `httptest`:
1. Set up a `gin.Engine` with `loopRoutes(app, api)` registered exactly as in `NewRouter`, with a fake/mock plugin registered in `app.GetLoopRegistry()` and a stub HTTP server standing in for the loopp process on `debug/pprof/symbol`.
2. Issue `httptest.NewRequest("POST", "/plugins/<name>/debug/pprof/symbol", body)` with no `Authorization` header, no session cookie, and no API token.
3. Assert the response status is `200 OK` and the body equals the stub plugin's forwarded symbol response — proving the request was serviced without any authentication check, contrasting with an equivalent request to `/v2/...` authenticated routes which returns `401 Unauthorized` for missing credentials via `auth.Authenticate`.

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
