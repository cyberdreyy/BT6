I have enough evidence to confirm the vulnerability.

### Title
Missing authentication (not merely missing role check) on LOOP plugin pprof/metrics endpoints allows any network client to trigger CPU profiling and metrics forwarding - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/debug/pprof/*profile`, `/plugins/:name/debug/pprof/symbol`, `/plugins/:name/metrics`, and `/discovery` routes are registered via `loopRoutes(app, api)` directly on the base `api` group, which only applies rate limiting and session-cookie parsing middleware — no `auth.Authenticate` wrapper and no `auth.RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` gate is ever applied. This is worse than the hypothesized "view-role can reach an admin-only op" scenario: the endpoint is reachable by a completely unauthenticated client, not just a low-privilege session holder.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the `api` group with only `rateLimiter(...)` and `sessions.Sessions(...)` middleware [1](#0-0) . `loopRoutes(app, api)` registers `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`, `pluginMetricHandler`, and `discoveryHandler` directly on this unauthenticated group [2](#0-1) . Contrast this with `metricRoutes(authv2)` at line 446, which nests the equivalent stdlib `/debug/pprof/*` handlers inside the `authv2` group that requires `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` [3](#0-2) , and with `debugRoutes`, which explicitly wraps `/debug/vars` in `auth.Authenticate(...)` [4](#0-3) . `pluginPPROFHandler` itself performs no session/role check — it looks up the plugin by name and forwards the request (including `seconds` query param controlling profile duration) to the internal LOOP process's `/debug/pprof/` endpoint and returns the raw response body to the caller [5](#0-4) . Because no `auth.Authenticate` middleware runs on this route at all, `gc.MustGet`-style user/role lookups are never invoked, so even a session cookie is unnecessary — any client that can reach the node's HTTP port can trigger `?seconds=30` (or higher) CPU profiling, or `pluginMetricHandler`/`discoveryHandler` for plugin metrics/service-discovery enumeration [6](#0-5) .

### Impact Explanation
An unauthenticated (or lowest-privilege) attacker can force the node to run sustained CPU profiling (`seconds` param, bounded only by `PPROFOverheadSeconds` + supplied value) against any registered LOOP plugin, and repeat it concurrently, causing a denial-of-service / resource-exhaustion condition on the node's LOOP plugin processes. It also discloses internal plugin metrics and internal-hostname/prometheus-port topology via `/discovery` and `/plugins/:name/metrics`, information that should only be available to trusted operators. This matches the "unauthorized profiling/DoS surface reachable by lowest-privilege (here: zero-privilege) authenticated user" impact class.

### Likelihood Explanation
No preconditions are required beyond network access to the node's web server — not even a valid session cookie, since the route bypasses `auth.Authenticate` entirely. This makes the issue trivially reproducible and repeatable by any external caller who can send `GET /plugins/:name/debug/pprof/profile?seconds=30`, provided at least one LOOP plugin is registered in `l.registry`.

### Recommendation
Wrap `loopRoutes` registration (or at minimum the `debug/pprof/*` and `symbol` sub-routes) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` and an appropriate role gate (`auth.RequiresAdminRole` for pprof/profile endpoints, consistent with how `/debug/pprof/*` under `authv2` is protected), mirroring the pattern used for `debugRoutes` and `metricRoutes(authv2)`.

### Proof of Concept
Go handler-level integration test (using `cltest` router setup) plan:
1. Build the router via `web.NewRouter(app, nil)` with a real `chainlink.Application` and a LOOP plugin registered in `app.GetLoopRegistry()`.
2. Issue `GET /plugins/{name}/debug/pprof/profile?seconds=30` with no `Cookie` header (or with a valid view-role session cookie).
3. Assert current behavior: response status is `200` (or reaches the plugin-forwarding code path with no 401/403), proving no authentication/role check is enforced.
4. Assert expected/fixed behavior: response should be `401 Unauthorized` (no session) or `403 Forbidden` (insufficient role), matching the protection applied to `authv2`'s `metricRoutes` pprof endpoints.
5. Repeat for `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/symbol` (POST), and `/discovery` to confirm the same gap across all `loopRoutes`.

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
