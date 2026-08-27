### Title
LOOP registry discovery/metrics/pprof endpoints mounted without authentication - ([File: core/web/router.go])

### Summary
`loopRoutes(app, api)` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the top-level `api` group with no `auth.Authenticate(...)` middleware, unlike every other route group (`debugRoutes`, `sessionRoutes`, `v2Routes`) which all wrap their handlers in `auth.Authenticate(...)`. Any unauthenticated caller who can reach the node's web server can enumerate registered LOOPP plugins and pull their pprof/metrics data.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the `api` group with only rate limiting and session middleware (no authentication), then mounts several route groups on it: [1](#0-0) 

Compare `debugRoutes`, which explicitly wraps its `/debug/vars` route in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`: [2](#0-1) 

and `sessionRoutes`/`v2Routes`, which similarly gate authenticated routes behind `auth.Authenticate(...)`: [3](#0-2) [4](#0-3) 

`loopRoutes`, however, registers its handlers directly on `r` (the `api` group) with zero auth middleware: [5](#0-4) 

`discoveryHandler` lists all registered LOOP plugins along with the discovery hostname and exposed Prometheus port, returning this to any caller: [6](#0-5) 

`pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` proxy arbitrary requests to the internal LOOP plugin's `/metrics` or `/debug/pprof/*` endpoints based on the attacker-supplied `:name` and `:profile` path parameters, and stream the plugin's internal Prometheus metrics or full Go pprof profile (heap dumps, goroutine stacks, CPU profiles) back to the caller: [7](#0-6) [8](#0-7) 

None of these handlers check session/token authentication or role, so any unauthenticated network client hitting the node's API port can enumerate plugin names, obtain internal LOOPP addresses/ports (via discovery), and pull raw pprof/metrics data that can reveal internal service topology, memory contents, or other sensitive runtime state.

### Impact Explanation
This is an authentication-bypass information-disclosure issue: an unauthenticated attacker can enumerate internal LOOP plugin names and addresses and pull Prometheus metrics and Go pprof profiles (heap/goroutine/cpu) of internal plugin processes proxied through the node — data that should require at least a view-role session per the "Authorization is exact" principle applied elsewhere in this file (e.g. `/debug/vars`). It does not directly enable fund movement or key extraction, but pprof heap dumps can leak sensitive in-memory data and internal network topology, aiding further attacks.

### Likelihood Explanation
No credentials are required at all — the attacker only needs network access to the node's HTTP API. All four routes (`/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, `/plugins/:name/debug/pprof/symbol`) are reachable with a simple unauthenticated GET/POST, making this trivially and repeatably exploitable whenever LOOP plugins are configured.

### Recommendation
Wrap `loopRoutes` handlers in the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (or token) middleware used by `debugRoutes`/`v2Routes`, and consider gating with a role check (e.g. `auth.RequiresAdminRole` or `auth.RequiresEditRole`) since pprof/metrics data is operationally sensitive, consistent with how `metricRoutes(authv2)` is already protected inside the authenticated `v2Routes` group.

### Proof of Concept
1. Build a Go handler-level integration test similar to existing `core/web/router_test.go` tests that spins up `NewRouter` with a mock `chainlink.Application` that has a non-empty `LoopRegistry`.
2. Issue an HTTP `GET /discovery` request with no `Authorization` header and no session cookie.
3. Assert response status is `200 OK` and body contains the registered plugin name(s) and `discoveryHostName:exposedPromPort`, proving unauthenticated enumeration.
4. Repeat for `GET /plugins/<name>/metrics` and `GET /plugins/<name>/debug/pprof/goroutine` with no auth headers, asserting `200 OK` and non-empty pprof/metrics payload is returned.
5. As a route-level assertion (per the audit's proof idea), iterate `engine.Routes()`, filter for the loop paths (`/discovery`, `/plugins/*`), and assert that `auth.Authenticate` (or any role-gate middleware) is present in each route's `HandlersChain` — this assertion currently fails, confirming the gap.

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

**File:** core/web/router.go (L216-217)
```go
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
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

**File:** core/web/loop_registry.go (L53-65)
```go
func (l *LoopRegistryServer) discoveryHandler(w http.ResponseWriter, req *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	groups := make([]*targetgroup.Group, 0, 1+len(l.registry.List()))

	// add node metrics to service discovery
	groups = append(groups, pluginGroup(l.discoveryHostName, l.exposedPromPort, "/metrics"))

	// add all the plugins
	for _, registeredPlugin := range l.registry.List() {
		group := pluginGroup(l.discoveryHostName, l.exposedPromPort, pluginMetricPath(registeredPlugin.Name))
		group.Labels[LabelMetaPluginName] = model.LabelValue(registeredPlugin.Name)
		groups = append(groups, group)
	}
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
