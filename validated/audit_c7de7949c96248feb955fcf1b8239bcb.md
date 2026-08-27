Confirmed: `loopRoutes` registers `GET /plugins/:name/debug/pprof/*profile` on the base `api` group with no `auth.Authenticate(...)` middleware applied, unlike the `debugRoutes` (`/debug/vars`) and `metricRoutes` (mounted under `authv2`) which are protected. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unauthenticated LOOP plugin pprof endpoint discloses process memory/runtime internals - ([File: core/web/router.go])

### Summary
The route `GET /plugins/:name/debug/pprof/*profile`, registered in `loopRoutes`, is mounted directly on the unauthenticated `api` group with no session/token middleware, unlike every other sensitive debug/admin route in `router.go`. Any unauthenticated caller who knows (or guesses/enumerates) a plugin name can invoke `LoopRegistryServer.pluginPPROFHandler` to retrieve live pprof profiles (heap, goroutine, cpu, etc.) of the internal LOOP plugin process.

### Finding Description
`NewRouter` builds a base `api` group with only rate-limiting and session-cookie middleware (no auth check), and calls `loopRoutes(app, api)` directly on it: [4](#0-3) . `loopRoutes` registers `pluginPPROFHandler` with zero auth wrapper: [1](#0-0) . Compare this to `debugRoutes`, which wraps `/debug/vars` in `auth.Authenticate(...)` [5](#0-4) , and `metricRoutes`, which is only mounted inside the authenticated `authv2` group for the node's own `/debug/pprof` [3](#0-2) .

`pluginPPROFHandler` looks up the plugin by name, builds an internal URL to `http://<loopHostName>:<PrometheusPort>/debug/pprof/<profile>`, and proxies the request/response verbatim to the caller without any authentication check: [6](#0-5) . The proxied `doRequest` returns the raw response body with `StatusOK` regardless of caller identity: [7](#0-6) .

An unauthenticated attacker only needs to know a registered plugin name (these are well-known LOOP plugin names such as `median`, `mercury`, `solana`, etc., often discoverable via `/discovery` which is also unauthenticated) and can send `GET /plugins/<name>/debug/pprof/heap` to receive a full heap dump of the plugin process — potentially containing key material, secrets, or other sensitive data resident in memory.

### Impact Explanation
This maps to Chainlink's "sensitive data exposure" / information disclosure impact class: unauthenticated retrieval of runtime memory/goroutine/cpu profiles of an internal LOOP plugin process. Heap and goroutine dumps can reveal secrets, private keys, or other in-memory sensitive data, and expose internal architecture (function names, stack traces) useful for further attacks. This does not itself provide fund movement or job impersonation but is a real disclosure of internal, potentially sensitive, process state to an unauthenticated network caller.

### Likelihood Explanation
No credentials, roles, or preconditions are required beyond knowing a plugin name, which is either fixed/well-known (e.g., configured LOOP plugin types) or enumerable via the also-unauthenticated `/discovery` endpoint on the same route group. The attack is a single unauthenticated GET request and is fully repeatable at will (e.g., repeatedly pulling heap dumps).

### Recommendation
Wrap `loopRoutes` (or at minimum the `pluginPPROFHandler` and `pluginPPROFPOSTSymbolHandler` routes) with the same `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession)` middleware used elsewhere in `router.go` (as done for `metricRoutes` under `authv2`), and consider requiring an admin/edit role via `auth.RequiresAdminRole` given the sensitivity of raw memory dumps.

### Proof of Concept
Go handler-level integration test:
1. Build the router via `NewRouter` (or a minimal test harness registering `loopRoutes` on an unauthenticated `gin.RouterGroup`) with a `LoopRegistry` containing one registered plugin (e.g., `"median"`) with a mock `EnvCfg.PrometheusPort` pointing at a `httptest.Server` that serves `/debug/pprof/heap`.
2. Send `httptest.NewRequest("GET", "/plugins/median/debug/pprof/heap", nil)` with **no** `Authorization` header and **no** session cookie.
3. Serve via the router's `ServeHTTP`.
4. Assert `w.Code == http.StatusOK` and that the response body matches the mock pprof payload — confirming the request was proxied and served without any 401/403 rejection, unlike a request to an `authv2`-protected route (e.g., `/v2/jobs`) which should return 401 under identical unauthenticated conditions.

### Citations

**File:** core/web/router.go (L77-91)
```go
	rl := config.WebServer().RateLimit()
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
