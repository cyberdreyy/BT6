Confirmed: `loopRoutes` registers `/discovery` on the `api` group with no auth middleware, so `discoveryHandler` is reachable without authentication and returns plugin names/scrape target info.

### Title
Unauthenticated disclosure of LOOP plugin names and internal Prometheus scrape targets via `/discovery` - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
The `/discovery` endpoint, registered by `loopRoutes` in `core/web/router.go`, is mounted on the outer `api` group without any `auth.Authenticate` middleware, unlike every other sensitive route (`/v2/*`, `/debug/*`, `/sessions`). As a result, any unauthenticated HTTP client can call `GET /discovery` and receive a JSON list of all registered LOOP plugin names and their internal Prometheus scrape target host:port addresses.

### Finding Description
In `core/web/router.go`, `NewRouter` builds an `api` group with only rate limiting and session middleware (`api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))`), then calls `loopRoutes(app, api)` [1](#0-0) . `loopRoutes` registers `r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))` with no `auth.Authenticate(...)` wrapper, in contrast to `debugRoutes`, `sessionRoutes`, and `v2Routes` which all explicitly wrap their groups with `auth.Authenticate` [2](#0-1) . `discoveryHandler` in `core/web/loop_registry.go` iterates `l.registry.List()` and builds Prometheus `targetgroup.Group` entries containing the node's host:port and, for every plugin, the plugin's metrics path and `__meta_plugin_name` label with the plugin's name, then serializes and writes this as the JSON response with no auth check inside the handler itself [3](#0-2) . Since no middleware intercepts the request before reaching this handler, any client with network access to the node's API port can enumerate installed LOOP plugin names and their internal target addresses without any credentials.

### Impact Explanation
This is an information disclosure of internal architecture: an unauthenticated attacker learns which LOOP plugins (e.g., specific relayer/median/medianpoR plugins) are running on the node and the internal hostname:port used for Prometheus scraping. This does not directly expose secrets, keys, or allow fund movement, but it aids reconnaissance for further attacks (e.g., targeting `/plugins/:name/metrics` or `/plugins/:name/debug/pprof/*` — note those routes also lack auth middleware in `loopRoutes`, though `pluginMetricHandler` only proxies to `localhost`/`loopHostName` internally so exploitability there is limited to what's exposed). The matching bounty class is low-severity "sensitive information disclosure without direct fund/key impact."

### Likelihood Explanation
Trivial and fully repeatable: no credentials, roles, or preconditions are required beyond network reachability to the node's HTTP API port, which is the same port serving the public UI/API. A single unauthenticated `GET /discovery` request immediately returns the data.

### Recommendation
Wrap the `/discovery`, `/plugins/:name/metrics`, and `/plugins/:name/debug/pprof/*` routes in `loopRoutes` with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` (or restrict them to an internal-only listener) consistent with the pattern used in `debugRoutes` and `v2Routes`.

### Proof of Concept
1. Build a `gin.Engine` via `NewRouter` (or a minimal router calling `loopRoutes`) with a test `chainlink.Application` mock whose `GetLoopRegistry()` returns a registry pre-populated with at least one plugin (e.g., "median").
2. Start `httptest.NewServer(engine)`.
3. Send `http.Get(server.URL + "/discovery")` with no `Authorization` header and no session cookie.
4. Assert response status is `200 OK` (not `401 Unauthorized`).
5. Decode JSON body as `[]*targetgroup.Group` and assert it contains a group with `Labels["__meta_plugin_name"] == "median"` and a `Targets` entry with the node's host:port, demonstrating unauthenticated disclosure.

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
