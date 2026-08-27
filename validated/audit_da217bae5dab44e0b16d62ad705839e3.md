### Title
Unauthenticated access to LOOP plugin Prometheus metrics via `/plugins/:name/metrics` proxy - ([File: core/web/router.go, core/web/loop_registry.go])

### Summary
The `loopRoutes` function registers `GET /plugins/:name/metrics` (along with `/discovery` and the pprof proxy routes) directly on the base `api` router group, which only carries rate-limiting and session middleware but no authentication requirement. In contrast, every other sensitive/debug endpoint in `router.go` (e.g. `/debug/vars`, `/debug/pprof/*` via `metricRoutes(authv2)`) is wrapped in `auth.Authenticate(...)`. This lets any unauthenticated network client fetch the internal Prometheus metrics of a registered LOOP plugin.

### Finding Description
`loopRoutes` is called with the `api` group in `NewRouter`: [1](#0-0) 

The `api` group only applies rate limiting and cookie sessions, not authentication middleware, and `loopRoutes` registers the handler with no additional auth wrapper: [2](#0-1) 

Compare this to the pprof metrics routes for the node itself, which are deliberately placed behind `auth.Authenticate` in the `authv2` group via `metricRoutes(authv2)`: [3](#0-2) 
and `debugRoutes`, which explicitly wraps `/debug/vars` with session authentication: [4](#0-3) 

`pluginMetricHandler` looks up the plugin by the `:name` path param, then proxies a GET request to the plugin's internal `/metrics` endpoint on `loopHostName:PrometheusPort`, and writes the raw response body back to the caller with no authentication check and no filtering of the content: [5](#0-4) 

Registered LOOP plugins (e.g., median, mercury, or other LOOPP relayer plugins) are added to the registry internally by the node during normal startup via `LoopRegistry.Register`, which is not attacker-controlled — the attacker cannot register arbitrary plugins remotely, but any legitimately running LOOP plugin's metrics become world-readable through this route since the plugin's `PrometheusPort` is otherwise not exposed externally: [6](#0-5) [7](#0-6) 

Because there is no auth middleware on this route group, any unauthenticated HTTP client reaching the node's API port can call `GET /plugins/<name>/metrics` and receive the proxied Prometheus metrics text of any registered LOOP plugin, and `/discovery` similarly leaks the list of registered plugin names and target hosts/ports without authentication.

### Impact Explanation
This matches the Chainlink bounty impact class of **information disclosure of internal node/plugin state** — Prometheus metrics can include operational counters, internal addresses/hostnames, plugin names, request rates, error counts, and other operational internals useful for reconnaissance or fingerprinting prior to a further attack. It does not directly expose private keys or funds, but it is a clear authentication-bypass on an endpoint that the code elsewhere treats as requiring authentication (compare `metricRoutes(authv2)` and `debugRoutes`), indicating this is a genuine gap rather than an intentionally public endpoint.

### Likelihood Explanation
Preconditions: zero credentials required; the attacker only needs network reachability to the node's HTTP API port (the same port used for all other, normally-authenticated, API routes, since `exposedPromPort` is set from `WebServer().HTTPPort()`). Any client that can reach `/v2/...` endpoints can equally reach `/plugins/:name/metrics` and `/discovery` unauthenticated. This is trivially repeatable — a single unauthenticated `GET` request.

### Recommendation
Wrap `loopRoutes` registrations with the same authentication middleware used for other debug/metrics endpoints (e.g., `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` or register them under `authv2`/`metricRoutes` similarly to `/debug/pprof`), so that `/discovery`, `/plugins/:name/metrics`, and the pprof proxy routes all require a valid authenticated session or token before proxying to internal LOOP plugin endpoints.

### Proof of Concept
Go handler-level integration test plan (using `httptest`):
1. Start an `httptest.NewServer` that serves a fixed body (e.g. `"plugin_test_metric 42\n"`) at `/metrics`, simulating the LOOP plugin's internal Prometheus endpoint.
2. Construct a `plugins.LoopRegistry` (e.g. `plugins.NewTestLoopRegistry`), and manually insert a `RegisteredLoop{Name: "fake-plugin", EnvCfg: loop.EnvConfig{PrometheusPort: <port of httptest server>}}` (or use `Register` and override the port to point at the local test server host/port).
3. Build the full `gin.Engine` via `NewRouter(app, nil)` (or directly construct a router calling `loopRoutes(app, group)`), with `app` mocked/stubbed to return the above registry, and with the normal `AuthenticationProvider` configured to require session/token auth on other routes.
4. Issue `httptest.NewRequest("GET", "/plugins/fake-plugin/metrics", nil)` **without** any `Authorization` header or session cookie, and record the response with `httptest.NewRecorder()`.
5. Assert: HTTP status `200 OK`, and response body equals the metrics text served by the fake plugin's `/metrics` endpoint — demonstrating the proxy is reachable and returns plugin data without any credential.
6. As a contrasting assertion, issue the same unauthenticated request to `/debug/vars` or an `authv2`-protected route and assert it returns `401/403`, confirming the inconsistency: those routes are protected while `/plugins/:name/metrics` is not.

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

**File:** core/web/router.go (L444-447)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** plugins/loop_registry.go (L78-96)
```go
func (m *LoopRegistry) Register(id string) (*RegisteredLoop, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if _, exists := m.registry[id]; exists {
		return nil, ErrExists
	}
	ports, err := freeport.Take(1)
	if err != nil {
		return nil, fmt.Errorf("failed to get free port: %w", err)
	}
	if len(ports) != 1 {
		return nil, errors.New("failed to get free port: no ports returned")
	}
	envCfg := loop.EnvConfig{
		AppID:            m.appID,
		FeatureLogPoller: m.featureLogPoller,
		PrometheusPort:   ports[0],
	}
```

**File:** plugins/loop_registry.go (L244-251)
```go
// Get plugin by id. Safe for concurrent use.
func (m *LoopRegistry) Get(id string) (*RegisteredLoop, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()

	p, exists := m.registry[id]
	return p, exists
}
```
