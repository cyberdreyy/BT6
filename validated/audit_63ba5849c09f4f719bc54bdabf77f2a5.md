### Title
Unauthenticated disclosure of internal LOOP plugin metrics and pprof debug data via `/plugins/:name/*` routes - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the base `api` router group without any `auth.Authenticate` middleware, unlike every other sensitive route group in `router.go` (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any unauthenticated network client can therefore GET plugin Prometheus metrics and pprof profiling data for any registered LOOP plugin.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the base group `api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))` with only rate limiting and session-store wiring — no authentication check [1](#0-0) . `loopRoutes(app, api)` then attaches its handlers straight to this unauthenticated `api` group: `r.GET("/discovery", ...)`, `r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)`, `r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)`, and `r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)` [2](#0-1) . By contrast, `debugRoutes` wraps `/debug/vars` in `auth.Authenticate(...)` [3](#0-2) , and all `v2Routes` sensitive endpoints are wrapped in `authv2 := r.Group("/v2", auth.Authenticate(...))` [4](#0-3) . `loopRoutes` has no equivalent wrapper.

`LoopRegistryServer.pluginMetricHandler` takes the `:name` path param, looks it up via `l.registry.Get(pluginName)`, and — if found — proxies a GET to the internal plugin's Prometheus endpoint and returns the raw response body to the caller with no additional authorization check [5](#0-4) . `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler` behave the same way but proxy to the plugin's `/debug/pprof/*` endpoints, including profile/heap/goroutine dumps and CPU profiling triggered via `seconds`/`gc` query params [6](#0-5) . None of these handlers check session cookies, API tokens, or EI credentials before proxying the request.

### Impact Explanation
An unauthenticated network attacker can enumerate/guess plugin names (or first call the also-unauthenticated `/discovery` endpoint, which lists all registered plugin names via `l.registry.List()` at `core/web/loop_registry.go:53-81`) and then pull internal Prometheus metrics and pprof debug/profile data (heap dumps, goroutine stacks, CPU profiles) for any LOOP plugin running on the node. This is unauthorized disclosure of internal node/plugin telemetry and runtime state to an outsider — falling under Chainlink's "unauthorized information disclosure" bounty class. pprof heap/goroutine dumps in particular can leak sensitive in-memory data (config values, key material references, internal addresses) and CPU profiling can be used to degrade node performance (self-inflicted DoS via repeated `seconds=`-controlled profiling requests).

### Likelihood Explanation
No credentials of any kind are required — the request only needs network access to the node's web server port. The `/discovery` endpoint conveniently enumerates all valid plugin names with zero auth, making exploitation trivial and fully repeatable at will (subject to global rate limiting only).

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/metrics` and `/plugins/:name/debug/pprof/*` routes) in an authenticated group, e.g. `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))`, consistent with `debugRoutes`. If external Prometheus scraping requires unauthenticated access to `/discovery`/`/plugins/:name/metrics`, gate them with a dedicated scrape token (similar to `prometheusHandler`'s bearer-token check) rather than leaving them fully open, and always require authentication for the pprof endpoints since they expose runtime debug data far more sensitive than metrics.

### Proof of Concept
Go handler-level integration test:
1. Build a `chainlink.Application` test double (or use existing `web` test helpers) with `GetLoopRegistry()` returning a registry containing one registered plugin (e.g., name `"median"`).
2. Call `web.NewRouter(app, nil)` to get the `*gin.Engine`.
3. Start `httptest.NewServer(router)`.
4. Send `GET /plugins/median/metrics` with no `Cookie`, no `Authorization`, and no EI headers.
5. Assert response status is `200 OK` (proxied from the plugin's metrics endpoint) rather than `401 Unauthorized`, demonstrating the missing auth check. Repeat for `GET /plugins/median/debug/pprof/heap` to confirm equivalent unauthenticated access to pprof data.
6. Contrast with `GET /debug/vars` in the same test, which should correctly return `401` without auth, confirming the auth gap is specific to `loopRoutes`.

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

**File:** core/web/loop_registry.go (L150-188)
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
