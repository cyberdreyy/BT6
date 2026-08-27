### Title
Unauthenticated pprof proxy exposes plugin process memory via `/plugins/:name/debug/pprof/*` - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
The `loopRoutes` registered in `core/web/router.go` mount `pluginPPROFHandler` directly on the unauthenticated `api` group, unlike the node's own `/v2/debug/pprof/*` routes which are wrapped in `authv2` (session/token authenticated). Any unauthenticated caller can hit `GET /plugins/:name/debug/pprof/*profile` and have the node proxy full pprof heap/goroutine/trace dumps from the LOOP plugin's internal process.

### Finding Description
In `core/web/router.go`, `NewRouter` calls `loopRoutes(app, api)` [1](#0-0)  where `api` is the top-level group with only rate-limiting/session middleware, no auth requirement [2](#0-1) . Inside `loopRoutes`, the pprof route is registered with no auth wrapper:
```go
r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
``` [3](#0-2) 

By contrast, the node's own `/debug/pprof` endpoints registered via `metricRoutes` are placed inside the `authv2` group requiring `auth.Authenticate` with token or session [4](#0-3) [5](#0-4) , and `debugRoutes`/`sessionRoutes` similarly wrap with `auth.Authenticate` [6](#0-5) [7](#0-6) .

`pluginPPROFHandler` looks up the named plugin in the registry, builds an internal URL `http://<loopHostName>:<PrometheusPort>/debug/pprof/<profile>` using attacker-controlled `profile` and query params (`debug`, `gc`, `seconds`), and proxies the response body verbatim back to the caller via `doRequest`/`gc.Data` [8](#0-7) [9](#0-8) . The `seconds` parameter directly controls how long the proxied request (and thus a CPU/trace profile) runs, up to `PPROFOverheadSeconds` plus attacker-supplied duration [10](#0-9) . No authentication, authorization, or role check occurs anywhere in this call path before the proxied request is issued and its body returned to the client.

### Impact Explanation
This is unauthenticated internal process memory disclosure: heap dumps (`?debug=2`), goroutine dumps, and CPU/execution traces of the LOOP plugin process can be pulled by any network-reachable, unauthenticated client. Depending on which plugin key material, seeds, or other secrets are resident in the plugin's memory (LOOP plugins can be OCR2, median, VRF, or other capability plugins), captured heap/goroutine data may include sensitive in-memory values or object graphs useful for further attacks, and at minimum reveals internal topology (`loopHostName`, `PrometheusPort`) and plugin behavior. It also allows a DoS vector: an attacker can force long-running `seconds=N` CPU/goroutine trace profiling on the plugin, consuming plugin resources repeatedly without any credentials.

### Likelihood Explanation
No preconditions beyond network access to the node's API port and knowledge (or brute-forcing) of a registered plugin name — trivially discoverable via the also-unauthenticated `/discovery` and `/plugins/:name/metrics` endpoints registered in the same `loopRoutes` block [11](#0-10) . The request is a single unauthenticated `GET`, fully repeatable, with no rate limiting beyond the generic `AuthenticatedPeriod`/`Authenticated` limiter applied to the whole `api` group.

### Recommendation
Wrap `loopRoutes` (or at minimum the pprof/metrics sub-routes) with the same `auth.Authenticate` (and appropriate role, e.g. `auth.RequiresAdminRole`) middleware used for `metricRoutes` under `authv2`, consistent with how the node's own `/debug/pprof` is protected.

### Proof of Concept
Go handler-level integration test in `core/web`:
1. Build a test `gin.Engine` using `NewRouter` with a mock `chainlink.Application` and a `LoopRegistry` containing a registered plugin `mockLoopImpl` pointing at a local `httptest.Server` that serves a fake `/debug/pprof/heap` handler.
2. Issue `GET /plugins/mockLoopImpl/debug/pprof/heap?debug=2` with no `Authorization` header and no session cookie.
3. Assert current behavior: response status `200` and body equals the fake plugin's pprof output — demonstrating the bypass.
4. After applying the fix (wrapping the route with `auth.Authenticate`), re-run the same request and assert `401 Unauthorized` is returned instead, and that with a valid session/token the `200` + proxied body is restored.

### Citations

**File:** core/web/router.go (L78-85)
```go
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)
```

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

**File:** core/web/router.go (L216-217)
```go
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
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

**File:** core/web/router.go (L238-248)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L130-148)
```go
const PPROFOverheadSeconds = 30

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
