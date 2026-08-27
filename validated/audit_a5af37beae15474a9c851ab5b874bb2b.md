### Title
Unauthenticated pprof/metrics data exposure via LOOP plugin routes lacking auth middleware - (File: core/web/router.go)

### Summary
The `loopRoutes` function registers `/plugins/:name/debug/pprof/*profile`, `/plugins/:name/debug/pprof/symbol` (POST), and `/plugins/:name/metrics` directly on the unauthenticated `api` route group without wrapping them in `auth.Authenticate`, unlike the analogous `/debug/vars` endpoint. Any caller who can reach the node's HTTP API can pull full pprof profiles (heap, goroutine, allocs, etc.) proxied from a running LOOP plugin process.

### Finding Description
In `core/web/router.go`, `NewRouter` builds an `api` group with only rate limiting and session middleware (no authentication), and passes it to several route registration functions: [1](#0-0) 

`loopRoutes` registers the pprof and metrics endpoints directly on this unauthenticated group: [2](#0-1) 

Contrast this with `debugRoutes`, which correctly wraps the equivalent core-node `/debug/vars` endpoint with `auth.Authenticate`: [3](#0-2) 

The handler `LoopRegistryServer.pluginPPROFHandler` looks up the named plugin from the registry, builds a proxy URL to the plugin's internal pprof HTTP port, and forwards the request with `doRequest`, returning the raw response body to the caller with no auth check: [4](#0-3) [5](#0-4) 

Because `api` (and thus `/plugins/:name/debug/pprof/*`) has no `auth.Authenticate` middleware anywhere in the chain, any client that can reach the node's HTTP API can issue `GET /plugins/{name}/debug/pprof/heap` (or `goroutine`, `profile`, `allocs`, etc.) and receive the full pprof dump for that LOOP plugin process without any credentials, session cookie, or API token.

### Impact Explanation
pprof heap/goroutine dumps can contain process memory contents including in-flight secrets, private key material, decrypted configuration, or other sensitive runtime state held by the LOOP plugin. Exposing this without authentication is a secrets/information disclosure issue and directly violates the "Secrets never leave" security property called out in the audit prompt. This corresponds to Chainlink's bounty impact class for information disclosure of sensitive node/plugin data.

### Likelihood Explanation
No credentials, roles, or special access are required — a plain unauthenticated HTTP client can reach the route as long as it can reach the node's web server, which is the same reachability assumption used for other node API endpoints (e.g., `/v2/resume/:runID`, `/query` before its own auth wrapper). The route is registered unconditionally with no build-tag or feature-flag gate, and it is trivially and repeatably reachable/scriptable.

### Recommendation
Wrap `loopRoutes` (or at minimum the pprof/metrics sub-routes) with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` similar to `debugRoutes` and `authv2`, and consider requiring an elevated role (e.g., `auth.RequiresAdminRole`) given the sensitivity of pprof dumps.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/loop_registry_test.go` style):
1. Start a `httptest.Server` acting as the fake LOOP plugin backend, serving `/debug/pprof/heap` with recognizable pprof-formatted content (as `net/http/pprof` would).
2. Register a `RegisteredLoop` in a `plugins.LoopRegistry` pointing `EnvCfg.PrometheusPort` at the fake backend's port, with `loopHostName` set to `localhost`.
3. Build the real chainlink `NewRouter`/`app` test harness (as in `TestLoopRegistry`) WITHOUT authenticating the HTTP client (i.e., use a raw `http.Client` with no session cookie/API token, not `app.NewHTTPClient`).
4. Issue `GET /plugins/mockLoopImpl/debug/pprof/heap` unauthenticated.
5. Assert the response is `200 OK` and the body matches the fake backend's pprof payload, with no `401`/`403` and no auth challenge header — proving the endpoint is reachable and returns plugin memory-profile data without authentication.

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
