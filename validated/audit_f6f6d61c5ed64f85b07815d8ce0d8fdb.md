### Title
Unauthenticated exposure of LOOP plugin pprof/metrics debug endpoints - ([File: core/web/router.go])

### Summary
`loopRoutes` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` on the `api` `*gin.RouterGroup`, which only has rate-limiting and session middleware applied — no `auth.Authenticate` wrapper is present. [1](#0-0)  Any unauthenticated network client that can reach the node's web server can therefore hit these routes and get the pprof handler to proxy arbitrary GET/POST requests to the internal LOOP plugin's `/debug/pprof/*` and `/metrics` endpoints. [2](#0-1) 

### Finding Description
`v2Routes`, `debugRoutes`, and `sessionRoutes` all explicitly wrap their groups with `auth.Authenticate(...)` (session/token/admin/edit/run role checks), e.g. `debugRoutes` uses `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` [3](#0-2) . In contrast, `loopRoutes(app, api)` is called directly on the top-level `api` group with no authentication middleware at all: [4](#0-3) . The `api` group itself only applies rate limiting and cookie session middleware (which doesn't reject unauthenticated sessions by itself), not an auth gate [1](#0-0) .

The handlers themselves proxy requests to the LOOP plugin's internal pprof/metrics HTTP server based on attacker-controlled `:name` and `:profile` path parameters plus query parameters (`debug`, `gc`, `seconds`) that are forwarded verbatim to the plugin's pprof endpoint: [5](#0-4) . `pluginPPROFPOSTSymbolHandler` also forwards an attacker-supplied POST body to `/debug/pprof/symbol` on the plugin: [6](#0-5) .

Because no authentication layer runs before these handlers, an unauthenticated remote client can:
- Enumerate registered LOOP plugin names and internal metrics endpoints via `/discovery` and `/plugins/:name/metrics`.
- Trigger expensive pprof profiling operations (e.g., `seconds=N` CPU/goroutine profiles, `debug/pprof/trace`) against the plugin's internal port, causing resource exhaustion / denial of service on that plugin process, with the wait time (`PPROFOverheadSeconds` + supplied `seconds`) fully attacker controllable.
- Potentially retrieve pprof debug output (goroutine stacks, heap dumps in some pprof debug modes) which can leak sensitive in-memory data (config values, addresses, internal state) from the plugin process, without any credential.

### Impact Explanation
This maps to a **DoS / information disclosure via unauthenticated debug endpoint** class: an unprivileged, unauthenticated network attacker can force resource-intensive profiling operations on LOOP plugin processes and can read profiling/metrics data that should require at least node authentication (comparable to `debugRoutes`'s own `/debug/vars`, which is gated by session auth). This is a real node-security regression, not merely a best-practice issue, because a functioning parallel route (`debugRoutes`) deliberately requires authentication for the equivalent `pprof`/expvar surface while `loopRoutes` does not.

### Likelihood Explanation
No credentials, roles, or tokens are required — any client capable of sending HTTP requests to the node's exposed web server (the same surface used for `/v2/...` API routes) can hit `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*`, and `/plugins/:name/debug/pprof/symbol`. The plugin name and profile type must be known or guessed, but `/discovery` itself unauthenticatedly enumerates registered plugin names, making this trivially and repeatably exploitable whenever any LOOP plugin (Median, plugin-based relayer, etc.) is registered on the node.

### Recommendation
Wrap the `loopRoutes` group with the same authentication middleware used elsewhere for debug/metrics surfaces, e.g.:
```go
loop := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))
loopRoutes(app, loop)
```
or move the calls inside `debugRoutes`'s authenticated group, and require at minimum `auth.RequiresAdminRole` for the pprof-forwarding handlers given the DoS potential of triggering long CPU/trace profiles.

### Proof of Concept
Handler-level integration test plan (Go, using `httptest` + the app test harness used elsewhere in `core/web`):
1. Build a `chainlink.Application` test fixture with `NewRouter` and a `plugins.LoopRegistry` containing one registered plugin (e.g. "median") pointing at a local `httptest.Server` simulating the plugin's `/debug/pprof/*` and `/metrics` endpoints.
2. Send `GET /discovery` with **no** `Authorization` header / no session cookie. Assert response is `200 OK` and body contains the plugin's discovery target (proving no auth is enforced), contrasting with a call to `GET /debug/vars` (also unauthenticated) which should return `401`/redirect due to `auth.Authenticate` in `debugRoutes`.
3. Send `GET /plugins/median/debug/pprof/profile?seconds=1` with no credentials; assert `200 OK` and that the request was forwarded to the backing plugin server (verify via a request counter/spy on the `httptest.Server`).
4. Send `POST /plugins/median/debug/pprof/symbol` with an arbitrary body and no credentials; assert it is proxied and returns `200 OK`.
5. Repeat step 2–4 against `/v2/keys/eth` (an `authv2` route) with no credentials and assert `401 Unauthorized`, demonstrating the inconsistency: `loopRoutes` bypasses the same auth gate applied to comparable sensitive routes.

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

**File:** core/web/loop_registry.go (L132-188)
```go
func pprofURLVals(gc *gin.Context) (urlVals url.Values, timeout time.Duration) {
	urlVals = make(url.Values)
	if db, ok := gc.GetQuery("debug"); ok {
		urlVals.Set("debug", db)
	}
	if gc, ok := gc.GetQuery("gc"); ok {
		urlVals.Set("gc", gc)
	}
	timeout = PPROFOverheadSeconds * time.Second
	if sec, ok := gc.GetQuery("seconds"); ok {
		urlVals.Set("seconds", sec)
		if i, err := strconv.Atoi(sec); err == nil {
			timeout = time.Duration(i+PPROFOverheadSeconds) * time.Second
		}
	}
	return urlVals, timeout
}

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

func (l *LoopRegistryServer) pluginPPROFPOSTSymbolHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/symbol", l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	body, err := io.ReadAll(gc.Request.Body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error reading plugin pprof request body: %s", err))
		return
	}
	l.doRequest(gc, "POST", pluginURL, bytes.NewReader(body), timeout, pluginName)
}
```
