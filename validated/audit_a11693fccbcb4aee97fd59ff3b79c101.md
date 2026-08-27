### Title
Unauthenticated disclosure of LOOP plugin registry topology via GET /discovery - (core/web/router.go)

### Summary
The `loopRoutes` function registers `/discovery`, `/plugins/:name/metrics`, and pprof-related plugin routes directly on the `api` router group without any `auth.Authenticate` middleware, unlike every other sensitive route group (`/v2`, `/debug`, `/sessions`). Any unauthenticated network client that can reach the node's public HTTP listener can call `GET /discovery` and receive a JSON list of all registered LOOP plugin names, their metrics paths, and the node's discovery hostname/port.

### Finding Description
In `core/web/router.go`, `NewRouter` builds a single top-level group `api` with only rate-limiting and session middleware attached (`core/web/router.go:78-91`): [1](#0-0) 

Compare this to other route groups that explicitly wrap sub-groups with `auth.Authenticate(...)`, e.g. `debugRoutes` (`core/web/router.go:180-183`), `sessionRoutes` (`core/web/router.go:216-217`), and `v2Routes` (`core/web/router.go:245-248`). `loopRoutes`, however, registers its handlers directly on the passed-in `r` group with no auth wrapper at all: [2](#0-1) 

`discoveryHandler` in `core/web/loop_registry.go` iterates `l.registry.List()` and writes a JSON array of Prometheus `targetgroup.Group` objects containing the node's discovery hostname, exposed Prometheus port, and every registered plugin name under the `__meta_plugin_name` label, with no authorization check performed inside the handler itself: [3](#0-2) 

Since `loopRoutes(app, api)` is called with the same `api` group that also serves other endpoints (`core/web/router.go:91`), and no middleware in the chain performs credential verification for this specific path, a raw unauthenticated `GET /discovery` reaches `discoveryHandler` and returns the full response.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated attacker can enumerate the names of all LOOP plugins running on the node and their metrics scrape paths/ports. This reveals internal topology (which plugins/relayers are configured) that could be used to inform further reconnaissance or targeting of the also-unauthenticated `/plugins/:name/metrics` and pprof-forwarding endpoints registered in the same function. It does not by itself leak credentials, private keys, or allow fund movement, so it falls under a low/informational disclosure impact class rather than a critical one.

### Likelihood Explanation
Likelihood is high in terms of reachability: no credentials, headers, or preconditions are required — any client with network access to the node's HTTP listener can trigger it repeatedly. The severity of what is disclosed (plugin names/ports, not secrets) is what limits the overall impact class.

### Recommendation
Wrap `loopRoutes`' route group with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for `/v2` and `/debug`, or otherwise restrict `/discovery` and the plugin forwarding endpoints to trusted internal callers (e.g., a separate internal-only listener) since they are intended for internal Prometheus scraping rather than the public API surface.

### Proof of Concept
1. Build the router via `NewRouter` with a mocked `chainlink.Application` whose `GetLoopRegistry()` returns a registry containing at least one plugin (e.g., named `"median"`).
2. Using `httptest.NewRecorder()` and `httptest.NewRequest(http.MethodGet, "/discovery", nil)` (no `Authorization` header, no session cookie), call `engine.ServeHTTP(recorder, req)`.
3. Assert `recorder.Code == http.StatusOK` (not `401/403`).
4. Assert the response body, when JSON-unmarshalled into `[]*targetgroup.Group`, contains a group with `Labels[LabelMetaPluginName] == "median"`, confirming plugin topology was disclosed to an unauthenticated caller.

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
