### Title
Unauthenticated LOOP Plugin pprof Proxy Endpoint Discloses Memory/Goroutine Profiling Data - ([File: core/web/loop_registry.go])

### Summary
`LoopRegistryServer.pluginPPROFHandler` is registered at `GET /plugins/:name/debug/pprof/*profile` via `loopRoutes` without any authentication middleware, unlike other sensitive routes in the router (`debugRoutes`, `sessionRoutes`) which are explicitly wrapped with `auth.Authenticate`. Any unauthenticated client can hit this route to have the node proxy a request to a running LOOP plugin's internal `net/http/pprof` endpoints (`heap`, `goroutine`, `cmdline`, `profile`, `trace`, etc.) and receive the raw response.

### Finding Description
In `core/web/router.go`, the `api` route group is created with only rate limiting and session middleware: [1](#0-0) 
`loopRoutes(app, api)` is called directly on this unauthenticated `api` group, in contrast to `debugRoutes` and `sessionRoutes`, which explicitly wrap their sensitive sub-groups with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`: [2](#0-1) [3](#0-2) 

`loopRoutes` registers `GET /plugins/:name/debug/pprof/*profile` directly to `pluginPPROFHandler` with no auth wrapper: [4](#0-3) 

`pluginPPROFHandler` looks up the named LOOP plugin in the registry, builds a URL to the plugin's internal pprof endpoint, and proxies the request/response verbatim to the caller: [5](#0-4) [6](#0-5) 

Because the `api` group and `loopRoutes` apply no session/token/role check, an unauthenticated network client that can reach the node's HTTP API can issue `GET /plugins/<name>/debug/pprof/heap` (or `goroutine`, `cmdline`, `profile`, `trace`) and receive the plugin's memory/goroutine dump or profiling data. This differs from the standalone node's own `/debug/pprof/*` routes registered via `metricRoutes`, which — while also seemingly unauthenticated in this router construction — are a separate, already-known surface; the question specifically concerns the LOOP-plugin proxy path, which forwards internal-network-only data (plugin process memory state) to any external caller without credential checks.

### Impact Explanation
Exposure of heap/goroutine/cmdline profiling data from a LOOP plugin process can leak process memory contents, including potentially sensitive in-memory state (keys, secrets held in plugin memory, internal data structures, stack traces revealing internal logic), to any unauthenticated party. This matches the Chainlink bounty impact class of "sensitive data disclosure" / "confinement of secrets or internal state" violation and provides reconnaissance value for further attacks against the node/plugin infrastructure.

### Likelihood Explanation
No credentials or preconditions are required beyond network reachability to the node's HTTP API — the same reachability needed for the unauthenticated `POST /sessions` login route. The route is fully deterministic and repeatable: any request to `/plugins/:name/debug/pprof/*profile` for a registered plugin name will be proxied and answered with `200 OK` and full profile bytes, as the handler contains no authentication or authorization check at all.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/debug/pprof/*` and `/plugins/:name/debug/pprof/symbol` sub-routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (and ideally `auth.RequiresAdminRole`) middleware used for `debugRoutes`, so pprof data can only be retrieved by authenticated, appropriately privileged users.

### Proof of Concept
Go handler-level integration test plan (using `httptest` + `gin`):
1. Construct a `chainlink.Application` mock/test app with `GetLoopRegistry()` returning a registry containing a fake plugin `mockLoopImpl` with `EnvCfg.PrometheusPort` pointing to a local `httptest.Server` that serves `net/http/pprof` handlers (simulating the LOOP plugin process).
2. Build the router via `web.NewRouter(app, nil)` (no session cookie, no `Authorization` header set on the request).
3. Issue `httptest.NewRequest("GET", "/plugins/mockLoopImpl/debug/pprof/heap", nil)` through the router.
4. Assert:
   - Response status is `200 OK` (not `401 Unauthorized`/`403 Forbidden`).
   - Response body matches/proxies the pprof heap profile bytes returned by the fake backend plugin server.
5. Repeat for `/debug/pprof/goroutine`, `/debug/pprof/cmdline`, and `/debug/pprof/profile?seconds=1` to confirm the same unauthenticated disclosure across all pprof sub-paths reachable via the wildcard `*profile` route.

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

**File:** core/web/loop_registry.go (L190-215)
```go
func (l *LoopRegistryServer) doRequest(gc *gin.Context, method, url string, body io.Reader, timeout time.Duration, pluginName string) {
	ctx, cancel := context.WithTimeout(gc.Request.Context(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin pprof request: %s", err))
		return
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		msg := "plugin pprof handler failed to post plugin url " + html.EscapeString(url)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q pprof", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
}
```
