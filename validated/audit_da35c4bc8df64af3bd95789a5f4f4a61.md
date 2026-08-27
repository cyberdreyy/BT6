Confirmed: `loopRoutes` registers `/plugins/:name/debug/pprof/*profile` on the shared `api` group at the top level of `NewRouter`, which only carries CORS, rate limiting, and security headers — no `auth.Authenticate` wrapper is applied, unlike the comparable `/debug/pprof` group under `authv2` (`metricRoutes(authv2)`) or the `/debug/vars` route (`debugRoutes`) which explicitly requires `auth.Authenticate(..., auth.AuthenticateBySession)`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unauthenticated pprof profile disclosure for LOOP plugins via `/plugins/:name/debug/pprof/*` - ([File: core/web/router.go])

### Summary
The `loopRoutes` function registers `pluginPPROFHandler` on the base `api` router group without any `auth.Authenticate` middleware, unlike the equivalent node-level pprof endpoints under `authv2`/`debugRoutes`. Any unauthenticated network client that can reach the node's HTTP API can request heap, goroutine, allocs, or other pprof profiles from a registered LOOP plugin process, which can leak process memory contents, addresses, and potentially embedded secrets.

### Finding Description
`NewRouter` builds a single `api` group with only CORS, rate limiting, security headers, and session middleware, then calls `loopRoutes(app, api)` alongside the other route groups. [2](#0-1) 
`loopRoutes` binds `GET /plugins/:name/debug/pprof/*profile` directly to `loopRegistry.pluginPPROFHandler` and `POST /plugins/:name/debug/pprof/symbol` to `pluginPPROFPOSTSymbolHandler`, with no `auth.Authenticate` wrapper applied anywhere in this function. [1](#0-0) 

By contrast, the equivalent internal pprof endpoints registered by `metricRoutes` are only mounted under `authv2`, which requires token or session authentication, and the `/debug/vars` route is explicitly wrapped in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`. [5](#0-4) [6](#0-5) 

`pluginPPROFHandler` looks up the named plugin in the LOOP registry, builds a forwarding URL to the plugin's internal pprof endpoint, and proxies the request via `doRequest`, returning the raw response body (heap dump, goroutine stacks, etc.) to the caller with `gc.Data(http.StatusOK, ...)`. [4](#0-3) [7](#0-6) 

Since no authentication middleware sits in front of this route, an unauthenticated attacker with network access to the node's HTTP API can pull full profiling data (heap contents, goroutine dumps) from any registered LOOP plugin, subject only to the plugin name being valid in the registry.

### Impact Explanation
This falls under credential/secret disclosure and information leakage. pprof heap and goroutine dumps can expose process memory contents including key material, internal addresses, and other sensitive data held in the LOOP plugin's memory, which is a meaningful information-disclosure vector against a node/plugin process without any credentials.

### Likelihood Explanation
The precondition is simply network reachability to the node's HTTP API and knowledge (or brute-force/enumeration) of a registered plugin name (plugin names are also disclosed unauthenticated via `/discovery` and `/plugins/:name/metrics`, which share the same lack of auth). No credentials, roles, or special access are required, making this trivially and repeatably exploitable by any unauthenticated caller with API access.

### Recommendation
Wrap `loopRoutes` (or at minimum the pprof-related routes: `pluginPPROFHandler` and `pluginPPROFPOSTSymbolHandler`) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used for `authv2`, consistent with how `metricRoutes` and `debugRoutes` require authentication for equivalent debug/pprof functionality.

### Proof of Concept
Go handler-level integration test plan:
1. Spin up a fake backend HTTP server exposing `/debug/pprof/heap` returning canned pprof bytes.
2. Construct a `LoopRegistryServer` with a registry containing one plugin whose `EnvCfg.PrometheusPort` points at the fake backend's port, and `loopHostName` set to `localhost`.
3. Build a `gin.Engine`, call `loopRoutes(app, api)` (or directly register `r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)`) without any auth middleware, mirroring production wiring.
4. Issue `httptest` `GET /plugins/{name}/debug/pprof/heap` with no `Authorization` header and no session cookie.
5. Assert response status is `200 OK` and body matches the fake backend's pprof payload, with no `401`/`403` challenge, demonstrating full unauthenticated access to the plugin's profiling data.

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

**File:** core/web/router.go (L180-199)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}

func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	pprofGroup.POST("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/trace", ginHandlerFromHTTP(pprof.Trace))
	pprofGroup.GET("/allocs", ginHandlerFromHTTP(pprof.Handler("allocs").ServeHTTP))
	pprofGroup.GET("/block", ginHandlerFromHTTP(pprof.Handler("block").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/mutex", ginHandlerFromHTTP(pprof.Handler("mutex").ServeHTTP))
	pprofGroup.GET("/threadcreate", ginHandlerFromHTTP(pprof.Handler("threadcreate").ServeHTTP))
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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
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
