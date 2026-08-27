### Title
Unauthenticated `/discovery` endpoint discloses LOOP plugin registry (names and metrics endpoints) - ([File: core/web/router.go])

### Finding Description
`loopRoutes()` in `core/web/router.go` registers `GET /discovery` directly on the `api` route group with only `ginHandlerFromHTTP(loopRegistry.discoveryHandler)`, with no `auth.Authenticate` middleware in the chain, unlike other sensitive routes such as `debugRoutes()` (which wraps `/debug` in `auth.Authenticate(...)`) or the `/v2` routes wrapped in `authv2`. [1](#0-0) 
The `api` group itself only applies rate limiting and session-cookie middleware, not authentication. [2](#0-1) 
`discoveryHandler` in `core/web/loop_registry.go` iterates `l.registry.List()` and returns a Prometheus-compatible JSON service-discovery document containing the hostname/port for the node's own metrics plus, for every registered LOOP plugin, a target group whose `__meta_plugin_name` label is set to the plugin's name and whose metrics path (`/plugins/<name>/metrics`) is included. [3](#0-2) 
Any unauthenticated network caller reaching this HTTP route can therefore enumerate all registered LOOP plugin names and the (predictable) per-plugin metrics scrape paths without any session/token/EI credential.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated caller can enumerate the names of internally-loaded LOOP plugins and derive their `/plugins/:name/metrics` scrape URLs. It does not by itself expose secrets, keys, or allow privilege escalation or fund movement — the corresponding `pluginMetricHandler`/`pluginPPROFHandler` routes proxy to internal LOOP endpoints keyed by plugin name from the registry, and the disclosed data is limited to plugin identity and metrics topology, not credentials. This maps to a low/informational disclosure of internal node topology rather than the "credential/key disclosure" or "unauthorized fund movement" bounty classes.

### Likelihood Explanation
Trivial and fully repeatable: no preconditions beyond network reachability to the node's web server port; a single unauthenticated `GET /discovery` request reveals the data every time, since the route registration has no auth middleware at all.

### Recommendation
Wrap `/discovery` (and consider the other LOOP registry routes) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)`, or restrict it to an internal-only listener/network segment (as is common for Prometheus service-discovery endpoints), consistent with how `debugRoutes()` protects `/debug/vars`.

### Proof of Concept
1. Register a fake `chainlink.Application` with a `LoopRegistry` containing at least one named plugin (e.g., via `plugins.NewLoopRegistry` + `RegisterLOOP`).
2. Build the router with `web.NewRouter(app, nil)` and start `httptest.NewServer(router)`.
3. Issue `http.Get(server.URL + "/discovery")` with no `Authorization` header and no session cookie.
4. Assert response status is `200 OK` and the JSON body (a `[]*targetgroup.Group`) contains a label `__meta_plugin_name` equal to the registered plugin's name, confirming disclosure without authentication.

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
