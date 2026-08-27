### Title
Unauthenticated disclosure of LOOP plugin pprof/heap/metrics data via `loopRoutes` - ([File: core/web/router.go])

### Summary
`loopRoutes(app, api)` is registered directly on the base `api` router group in `NewRouter` with no authentication middleware wrapper, unlike `debugRoutes` which requires `auth.Authenticate(..., auth.AuthenticateBySession)`. This allows any unauthenticated client to reach `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol`, exposing plugin pprof heap/goroutine/cmdline dumps and metrics data.

### Finding Description
In `NewRouter`, `api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))` creates a base group with only rate limiting and session middleware — no authentication gate is applied at that level. `loopRoutes(app, api)` is then called directly on this group: [1](#0-0) , registering `GET /discovery`, `GET /plugins/:name/metrics`, `GET /plugins/:name/debug/pprof/*profile`, and `POST /plugins/:name/debug/pprof/symbol` with no auth wrapper: [2](#0-1) .

By contrast, `debugRoutes` wraps its `/debug` group in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` before exposing `expvar`: [3](#0-2) . Likewise the equivalent `/v2/...` pprof routes (`metricRoutes`) are only mounted inside the authenticated `authv2` group: [4](#0-3) .

The handlers themselves (`discoveryHandler`, `pluginMetricHandler`, `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler` in `core/web/loop_registry.go`) perform no authentication or session checks of their own — they only validate that the named plugin exists via `l.registry.Get(pluginName)` before proxying the request to the plugin's internal pprof/metrics endpoint and returning the raw response body to the caller: [5](#0-4) [6](#0-5) . There is no code path anywhere between the router registration and the handler body that enforces a session, API token, or role check, so any client with network access to the node's HTTP port can pull heap/goroutine memory dumps or command-line arguments of LOOP plugin processes, which may contain secrets, keys, or other sensitive in-memory data.

### Impact Explanation
This is an unauthenticated information-disclosure vulnerability: an external, unauthenticated attacker can retrieve pprof heap/goroutine/cmdline dumps and plugin metrics data from a running Chainlink node's LOOP plugins. Heap/goroutine dumps can contain sensitive in-memory data (potentially including secrets or key material depending on plugin memory layout), and `/debug/pprof/profile` and unauthenticated repeated dumps can also be used as a resource-exhaustion vector. This matches the "sensitive data exposure without authentication" / "unauthenticated disclosure of node internals" bounty impact class.

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's API port — no session cookie, no API token, no role. The attacker only needs to know or guess a valid plugin name (or observe it via the unauthenticated `/discovery` endpoint itself, which lists registered LOOP plugin names) and issue a single unauthenticated GET request. This is fully repeatable and trivially reproducible.

### Recommendation
Wrap `loopRoutes` registration in an authenticated (and ideally admin/edit-role-gated) subgroup, consistent with `debugRoutes` and the authenticated `metricRoutes` mounted under `authv2`. For example, register these routes under a group using `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (and consider `auth.RequiresAdminRole` given the sensitivity of pprof/heap data), rather than directly on the unauthenticated base `api` group.

### Proof of Concept
Add a handler-level integration test in `core/web/router_test.go` (or a new test file) that:
1. Builds the router via `NewRouter` with a mock `chainlink.Application` whose `GetLoopRegistry()` returns a registry containing one registered plugin (e.g., name `"testplugin"` with a valid `EnvCfg.PrometheusPort` pointing at an httptest backend serving fake pprof/metrics data).
2. Uses `httptest.NewServer(router)` and issues:
   - `GET /discovery` with no `Cookie`/`Authorization` header — assert response is `200 OK` (expected to fail, i.e., should require auth).
   - `GET /plugins/testplugin/metrics` with no auth headers — assert `200 OK` with plugin metrics body returned.
   - `GET /plugins/testplugin/debug/pprof/heap` with no auth headers — assert `200 OK` with pprof binary dump body returned.
   - `POST /plugins/testplugin/debug/pprof/symbol` with no auth headers — assert `200 OK`.
3. Compare against the equivalent authenticated route `GET /v2/keys/csa` (or similar `authv2` route) issued without a session, which should return `401 Unauthorized`, demonstrating the inconsistency: `loopRoutes` endpoints return `200` while structurally similar authenticated endpoints correctly reject unauthenticated callers.
4. After applying the fix (wrapping `loopRoutes` in an authenticated group), re-run the same requests and assert they now return `401 Unauthorized` without a valid session/token, and `200` when a valid session cookie or API token is supplied.

### Citations

**File:** core/web/router.go (L87-91)
```go
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
