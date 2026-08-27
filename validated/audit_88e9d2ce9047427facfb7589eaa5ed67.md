Confirmed: `loopRoutes` is registered on the `api` group at line 91 of `core/web/router.go`, which only has rate-limiting and session middleware applied (line 78-85) — no `auth.Authenticate` wrapper is attached to `/discovery`, `/plugins/:name/metrics`, or the pprof plugin routes, unlike every other sensitive route (`v2Routes`, `debugRoutes`, `sessionRoutes` DELETE) which explicitly wrap their groups with `auth.Authenticate(...)`.

### Title
Unauthenticated disclosure of internal LOOP plugin Prometheus metrics via `/plugins/:name/metrics` - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
The route `GET /plugins/:name/metrics`, registered in `loopRoutes` [1](#0-0) , is mounted on the bare `api` router group which lacks any authentication middleware, unlike all other sensitive route groups in `router.go`. Any unauthenticated network client that can reach the node's web server can invoke `LoopRegistryServer.pluginMetricHandler`, which proxies and returns the raw `/metrics` output of the internal LOOP plugin process for any valid plugin name.

### Finding Description
`NewRouter` creates the `api` group with only rate limiting and session-cookie middleware attached [2](#0-1) . `loopRoutes(app, api)` registers `/discovery`, `/plugins/:name/metrics`, and the pprof plugin routes directly on this unauthenticated group with no additional `auth.Authenticate(...)` wrapper [1](#0-0) . Contrast this with `debugRoutes`, `sessionRoutes`, and `v2Routes`, which explicitly gate their groups behind `auth.Authenticate(app.AuthenticationProvider(), ...)` [3](#0-2) [4](#0-3) .

`pluginMetricHandler` looks up the plugin by the `:name` path param via `l.registry.Get(pluginName)`, then issues an HTTP GET to the internal plugin's `/metrics` endpoint (`http://<loopHostName>:<PrometheusPort>/metrics`) and writes the raw response body straight back to the client with no filtering or redaction [5](#0-4) . There is no session/token check anywhere in this call path, so any request that resolves a registered plugin name succeeds and returns proxied metrics content to an anonymous caller.

### Impact Explanation
This is an authentication-bypass information-disclosure issue: internal runtime/operational metrics of LOOP plugin processes (which can include process/resource telemetry, chain/relayer identifiers, and other internal state exposed via Prometheus metrics) are disclosed to any unauthenticated network client able to reach the node's HTTP port. This matches the "sensitive data exposure due to missing authentication" bounty class rather than fund loss or key disclosure directly, but it materially aids reconnaissance against the node (topology, active plugins, internal port/service names, potential further pivoting).

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's web server and knowledge/guessability of a registered plugin name (which is itself discoverable unauthenticated via the also-unprotected `/discovery` endpoint on the same route group). The exploit is a single unauthenticated `GET` request and is fully repeatable/deterministic.

### Recommendation
Wrap the `loopRoutes` group (or at minimum `/plugins/:name/metrics` and the pprof proxy routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for `v2Routes`/`debugRoutes`, and consider applying an admin-only role check (`auth.RequiresAdminRole`) given the operational sensitivity of pprof/metrics data.

### Proof of Concept
Go handler-level integration test using `web.NewRouter`:
1. Build a test `chainlink.Application` with a `LoopRegistry` containing one registered plugin (e.g., name `"test-plugin"`) whose `PrometheusPort` points to a local `httptest.Server` that serves canned Prometheus text on `/metrics`.
2. Construct the full router via `NewRouter(app, nil)` (mirrors production wiring) with no `Authorization` header/session cookie set.
3. Issue `httptest.NewRequest(http.MethodGet, "/plugins/test-plugin/metrics", nil)` through the router.
4. Assert response status is `200 OK` (not `401 Unauthorized`) and the response body equals/contains the canned metrics text served by the fake plugin backend — demonstrating unauthenticated disclosure.
5. As a control, repeat against `/v2/jobs` on the same unauthenticated request to confirm it returns `401`, proving the discrepancy is specific to the `loopRoutes` group.

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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/loop_registry.go (L96-128)
```go
func (l *LoopRegistryServer) pluginMetricHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/metrics", l.loopHostName, p.EnvCfg.PrometheusPort)
	req, err := http.NewRequestWithContext(gc.Request.Context(), http.MethodGet, pluginURL, nil)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin metrics request: %s", err))
		return
	}
	res, err := l.promClient.Do(req)
	if err != nil {
		msg := "plugin metric handler failed to get plugin url " + html.EscapeString(pluginURL)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q metrics", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
}
```
