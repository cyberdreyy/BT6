### Title
LOOP plugin registry endpoints (`/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*`) are registered with no authentication or role check at all, exposing plugin inventory to any caller - ([File: core/web/router.go])

### Summary
`loopRoutes` registers the LOOP discovery/metrics/pprof endpoints directly on the base `api` router group with no `auth.Authenticate` wrapper and no `auth.RequiresEditRole`/`auth.RequiresAdminRole` guard, unlike every other sensitive endpoint in `v2Routes` and unlike `debugRoutes`, which explicitly requires an authenticated session. Because there is no auth check whatsoever, any caller — including a view-role session, or even an unauthenticated client — can enumerate registered plugin names via `/discovery` and pull raw `/metrics` (which for Go binaries typically include build info such as version) via `/plugins/:name/metrics`.

### Finding Description
In `core/web/router.go`, `loopRoutes` is called with the plain `api` group (only rate-limiting + session middleware, no auth): [1](#0-0) [2](#0-1) 

Compare this to `debugRoutes`, which is explicitly gated behind `auth.Authenticate(..., auth.AuthenticateBySession)`: [3](#0-2) 

and to the rest of `v2Routes`, where every state-changing or sensitive read is wrapped in `auth.RequiresEditRole`/`auth.RequiresAdminRole`/`auth.RequiresRunRole` inside an `authv2` group that itself requires token or session authentication: [4](#0-3) 

The LOOP handlers themselves do no authorization check — `discoveryHandler` iterates `l.registry.List()` and returns plugin names/metrics paths as JSON, and `pluginMetricHandler`/`pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler` proxy directly to the internal plugin's `/metrics` or `/debug/pprof/*` endpoint based solely on the unauthenticated `:name` path parameter: [5](#0-4) [6](#0-5) [7](#0-6) 

Since no middleware validates a session or role before reaching these handlers, a caller with only a view-role session (or no credentials at all) can GET `/discovery` and `/plugins/:name/metrics`, retrieving the list of registered LOOP plugin names and their proxied Prometheus metrics (which commonly expose Go runtime/build-info metrics including version strings), and can also reach `/plugins/:name/debug/pprof/*` for runtime profiling data of the plugin process. This is a straightforward authorization gap: the intended edit/admin gate described in project scope documentation is simply absent from the route registration.

### Impact Explanation
This maps to an information disclosure impact: exposure of the internal LOOP plugin inventory (names), and via proxied `/metrics` and `/debug/pprof` endpoints, potential exposure of build/version info and runtime profiling data that assist targeted exploitation against a specific relayer/plugin version. It does not directly enable fund movement or key disclosure, so it is a lower-severity infrastructure/information-disclosure finding, but it is a real authorization defect since these routes have zero access control versus the documented edit/admin gating expectation.

### Likelihood Explanation
Likelihood is high and trivial to reproduce: no valid credentials of any kind are required, let alone an edit/admin role. Any unauthenticated or low-privilege (view-role) client that can reach the node's HTTP API can call `GET /discovery` or `GET /plugins/:name/metrics` directly.

### Recommendation
Wrap `loopRoutes` registration with an authenticated group requiring at minimum an edit/admin role, consistent with the rest of `v2Routes`, e.g.:
```go
loopGroup := r.Group("/", auth.Authenticate(app.AuthenticationProvider(),
    auth.AuthenticateByToken, auth.AuthenticateBySession))
loopGroup.GET("/discovery", auth.RequiresAdminRole(ginHandlerFromHTTP(loopRegistry.discoveryHandler)))
loopGroup.GET("/plugins/:name/metrics", auth.RequiresAdminRole(loopRegistry.pluginMetricHandler))
loopGroup.GET("/plugins/:name/debug/pprof/*profile", auth.RequiresAdminRole(loopRegistry.pluginPPROFHandler))
loopGroup.POST("/plugins/:name/debug/pprof/symbol", auth.RequiresAdminRole(loopRegistry.pluginPPROFPOSTSymbolHandler))
```

### Proof of Concept
Go handler-level integration test plan (in `core/web`, using existing test harness patterns from `router_test.go`/`loop_registry_internal_test.go`):
1. Build the router via `NewRouter` with a test `chainlink.Application` that has a populated `LoopRegistry` (register a fake plugin, e.g. name `"median"`).
2. Case A (no session/token): send `GET /discovery` with no `Authorization` header and no session cookie. Assert response status `200 OK` and body contains the registered plugin name — demonstrating no auth is enforced at all.
3. Case B (view-role session): authenticate as a view-role user, send `GET /discovery` and `GET /plugins/median/metrics` with the view-role session cookie. Assert `200 OK` and plugin data returned, confirming a view-role session can access data that per the scope description should require edit/admin.
4. Case C (control): repeat against an endpoint properly gated, e.g. `GET /v2/keys/eth/export/:address` with `auth.RequiresAdminRole`, using a view-role session, and assert `401`/`403` is returned — establishing the expected authorization behavior that `loopRoutes` fails to replicate.

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

**File:** core/web/router.go (L245-254)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
		uc := UserController{app}
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
```

**File:** core/web/loop_registry.go (L52-65)
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
```

**File:** core/web/loop_registry.go (L96-127)
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
