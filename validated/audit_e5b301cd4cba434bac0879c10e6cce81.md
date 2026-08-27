### Title
Unauthenticated LOOP plugin registry disclosure via `/discovery` endpoint - (File: core/web/loop_registry.go)

### Summary
`loopRoutes()` in `core/web/router.go` registers `GET /discovery` (and the related `/plugins/:name/...` endpoints) directly on the base `api` router group with no `auth.Authenticate` middleware, unlike every other sensitive route group (`debugRoutes`, `sessionRoutes`, `v2Routes`) which explicitly wrap themselves in an authenticated sub-group. Any unauthenticated caller can hit `/discovery` and receive the full list of registered LOOP plugin names, the node's Prometheus port, and its internal/discovery hostname.

### Finding Description
In `core/web/router.go`, `NewRouter` builds an `api` group with only rate limiting and session middleware attached, then calls `loopRoutes(app, api)` [1](#0-0) . Inside `loopRoutes`, the discovery route is registered with no auth wrapper at all: `r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))` [2](#0-1) . Compare this to `debugRoutes`, `sessionRoutes`, and `v2Routes`, which all create sub-groups guarded by `auth.Authenticate(...)` before exposing anything [3](#0-2) [4](#0-3) [5](#0-4) .

`discoveryHandler` itself performs no authentication or authorization check; it iterates `l.registry.List()` and returns a JSON array of Prometheus `targetgroup.Group` entries, one for the node's own `/metrics` endpoint and one per registered LOOP plugin, each labeled with `LabelMetaPluginName` (the plugin name), the `discoveryHostName`, and `exposedPromPort` [6](#0-5) [7](#0-6) . Since the route sits on the unauthenticated `api` group, any anonymous HTTP client can `GET /discovery` and receive this data without credentials.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated caller learns the node's internal architecture — the exact names of all registered LOOP plugins, the Prometheus scrape port, and the discovery hostname used for internal/external Prometheus scraping. This is reconnaissance-grade internals disclosure (plugin inventory + network topology) that could facilitate further targeted attacks (e.g., against the plugin-specific pprof/metrics endpoints, which are also unauthenticated on the same route group). It does not by itself grant fund movement, key disclosure, or job execution, so it maps to a low/informational disclosure class rather than a critical/high impact.

### Likelihood Explanation
Trivial and fully reproducible: no credentials, tokens, or session are required; a single unauthenticated `GET /discovery` request against any running Chainlink node returns the data every time the route is reachable. No preconditions beyond network reachability to the node's web server.

### Recommendation
Wrap `loopRoutes` registrations in an authenticated group, consistent with `debugRoutes`/`v2Routes` (e.g., `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))`), or otherwise restrict `/discovery` and `/plugins/:name/*` to trusted internal network paths / a dedicated internal-only listener rather than the public API router.

### Proof of Concept
Go handler-integration test plan:
1. Build a `chainlink.Application` mock/stub with `GetLoopRegistry()` returning a `*plugins.LoopRegistry` populated with at least one registered plugin (e.g., name `"median"`, some `PrometheusPort`).
2. Call `NewRouter(app, nil)` to construct the full `gin.Engine` (as done in existing router tests), without performing any session/login/token setup.
3. Issue `httptest.NewRequest("GET", "/discovery", nil)` with no `Authorization` header and no session cookie, dispatch via `router.ServeHTTP(w, req)`.
4. Assert `w.Code == http.StatusOK` (not 401/403) and unmarshal `w.Body` into `[]*targetgroup.Group`, asserting it contains an entry whose `Labels[web.LabelMetaPluginName] == "median"` and `Targets` contains the node's `exposedPromPort`.
5. Contrast with an authenticated-only route (e.g., `/v2/users`) in the same test file to show it returns 401 without auth, while `/discovery` returns 200, demonstrating the missing-auth gap is specific to `loopRoutes`.

### Citations

**File:** core/web/router.go (L86-92)
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

**File:** core/web/router.go (L207-218)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
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

**File:** core/web/loop_registry.go (L52-81)
```go
// discoveryHandler implements service discovery of prom endpoints for LOOPs in the registry
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

	b, err := l.jsonMarshalFn(groups)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, err = w.Write([]byte(err.Error()))
		if err != nil {
			l.logger.Error(err)
		}
		return
	}
	_, err = w.Write(b)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		l.logger.Error(err)
	}
}
```

**File:** core/web/loop_registry.go (L83-93)
```go
func pluginGroup(hostName string, port int, path string) *targetgroup.Group {
	return &targetgroup.Group{
		Targets: []model.LabelSet{
			// target address will be called by external prometheus
			{model.AddressLabel: model.LabelValue(fmt.Sprintf("%s:%d", hostName, port))},
		},
		Labels: map[model.LabelName]model.LabelValue{
			model.MetricsPathLabel: model.LabelValue(path),
		},
	}
}
```
