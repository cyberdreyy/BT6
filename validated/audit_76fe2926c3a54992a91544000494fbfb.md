### Title
Unauthenticated disclosure of internal LOOP plugin Prometheus metrics via `GET /plugins/:name/metrics` - ([File: core/web/loop_registry.go])

### Summary
The route `GET /plugins/:name/metrics`, registered in `loopRoutes` and dispatched to `LoopRegistryServer.pluginMetricHandler`, is mounted on the base `api` router group which only applies rate limiting and session middleware — no `auth.Authenticate` wrapper is applied to this route or its sibling LOOP endpoints. Any network client that can reach the node's HTTP port can retrieve internal LOOP plugin Prometheus metrics without any credentials.

### Finding Description
In `core/web/router.go`, the top-level `api` group is created with only rate limiting and `sessions.Sessions` middleware: [1](#0-0) 
`loopRoutes` is called on this unauthenticated `api` group and registers `r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)` along with `/discovery` and pprof-related routes, none guarded by `auth.Authenticate`: [2](#0-1) 
This is unlike every other sensitive route group (`v2Routes`, `debugRoutes`, `sessionRoutes`) which explicitly wrap handlers with `auth.Authenticate(...)` and role checks, e.g.: [3](#0-2) 
The handler itself, `pluginMetricHandler`, performs no authorization check — it looks up the plugin by name and proxies the request to the plugin's internal metrics port, returning the raw body to the caller: [4](#0-3) 
Consequently, an unauthenticated attacker who can reach the node's HTTP port and knows (or discovers, e.g. via `/discovery`, which is also unauthenticated) a valid LOOP plugin name can fetch that plugin's Prometheus metrics without any session cookie or API token.

### Impact Explanation
This exposes internal LOOP plugin telemetry (Prometheus metrics), which can include operational details, counters, labels, and potentially chain/job-related identifying information depending on what the plugin exports. This matches an information-disclosure impact class (unauthorized read of internal node/plugin operational data) rather than a direct fund-movement or key-disclosure bug, but it violates the stated invariant that "sensitive internal endpoints require auth" and provides reconnaissance value to an attacker (plugin names, active loop configuration, discovery endpoint list at `/discovery`).

### Likelihood Explanation
No preconditions beyond network reachability to the node's HTTP port are required; no credentials, roles, or tokens are needed. The `/discovery` endpoint (also unauthenticated) can be used to enumerate valid plugin names, making the attack fully self-contained and trivially repeatable via a single `GET` request.

### Recommendation
Wrap the LOOP registry routes with the same `auth.Authenticate` (and appropriate role, e.g. `auth.RequiresAdminRole` or `auth.RequiresRunRole`) middleware used elsewhere in `router.go`, e.g. register them under a group like:
`r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))` before mounting `/discovery`, `/plugins/:name/metrics`, and the pprof forwarding routes in `loopRoutes`.

### Proof of Concept
Go handler-integration test plan:
1. Build a `chainlink.Application` mock/test app with `GetLoopRegistry()` returning a registry containing one registered plugin (e.g., name `"median"`) and a fake backing HTTP server serving `/metrics`.
2. Call `web.NewRouter(app, nil)` to construct the full `*gin.Engine`.
3. Issue `httptest.NewRequest("GET", "/plugins/median/metrics", nil)` with **no** `Authorization` header and **no** session cookie, run it through the router via `httptest.NewRecorder()`.
4. Assert `recorder.Code == http.StatusOK` and that the response body matches the fake plugin's metrics payload, proving the endpoint is reachable and returns internal data without authentication — contrasted against an equivalent request to an authenticated route (e.g. `/v2/keys/eth`) which should return `401`/`403` without credentials.

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
