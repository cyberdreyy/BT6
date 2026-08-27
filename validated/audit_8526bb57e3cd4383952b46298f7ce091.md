### Title
Unauthenticated disclosure of internal LOOP plugin Prometheus metrics via `GET /plugins/:name/metrics` - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `GET /plugins/:name/metrics` (and the related `/discovery` and pprof plugin routes) directly on the top-level `api` router group, which only has rate-limiting and session middleware attached — no `auth.Authenticate` wrapper is applied, unlike every other sensitive route registered via `authv2` or `debugRoutes`. This allows any unauthenticated network client to retrieve internal LOOP plugin Prometheus metrics.

### Finding Description
In `NewRouter`, the `api` group is created with only rate limiting and session middleware, no authentication: [1](#0-0) . `loopRoutes(app, api)` is called on this un-authenticated group, registering `r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)` with no auth middleware at all: [2](#0-1) . This contrasts with other internal/debug routes in the same file, such as `debugRoutes`, which wraps `/debug/vars` in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [3](#0-2) , and `metricRoutes(authv2)` for pprof, which is only reachable through the already-authenticated `authv2` group [4](#0-3) .

The handler `pluginMetricHandler` looks up the plugin by name in the registry and proxies a GET request to the plugin's internal Prometheus endpoint, returning the raw metrics body to the caller with no additional access check: [5](#0-4) . Since the route itself has no auth wrapper, any unauthenticated client that knows (or guesses/enumerates) a registered LOOP plugin name can retrieve its internal metrics without any credential.

### Impact Explanation
This is an authentication-bypass / unauthorized information disclosure issue: internal LOOP plugin runtime metrics and labels (which can reveal internal operational details, plugin names, and potentially other sensitive operational metadata) are exposed to any unauthenticated outsider who can reach the node's web server. This matches Chainlink bounty impact class "sensitive information disclosure" via missing authentication on an internal debug/monitoring endpoint.

### Likelihood Explanation
No credentials are required — a plain unauthenticated `GET` request suffices. Plugin names can be discovered via the equally unauthenticated `/discovery` endpoint registered in the same `loopRoutes` function [6](#0-5) , making the attack trivially repeatable by any network client that can reach the node's HTTP port.

### Recommendation
Wrap the `/plugins/:name/metrics` route (and the sibling `/discovery` and pprof plugin routes) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`, consistent with how `debugRoutes` and `metricRoutes` protect other internal diagnostic endpoints, or move these routes under the authenticated `authv2` group.

### Proof of Concept
1. Build a `gin.Engine` via `NewRouter` with a test `chainlink.Application` whose `GetLoopRegistry()` returns a registry containing a plugin, e.g. `mockLoopImpl`, with a mocked Prometheus metrics server backing it (as done in `core/web/loop_registry_internal_test.go`).
2. Start `httptest.NewServer(router)`.
3. Issue `http.Get(server.URL + "/plugins/mockLoopImpl/metrics")` with no `Authorization` header and no session cookie.
4. Assert: current behavior returns `200 OK` with the plugin's metrics body; expected/secure behavior should be `401 Unauthorized`.
5. Repeat for `/discovery` to confirm plugin name enumeration is also unauthenticated, establishing full exploit chain (discover plugin name → fetch its metrics), both without any credential.

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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
