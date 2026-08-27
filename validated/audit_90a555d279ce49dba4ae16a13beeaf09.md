### Title
Unauthenticated cross-plugin Prometheus metrics disclosure via `/plugins/:name/metrics` - ([File: core/web/router.go], [File: core/web/loop_registry.go])

### Summary
`loopRoutes` registers `GET /plugins/:name/metrics`, `GET /plugins/:name/debug/pprof/*profile`, and `POST /plugins/:name/debug/pprof/symbol` directly on the shared `api` route group with no `auth.Authenticate` wrapper, unlike every other sensitive group in the router (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any unauthenticated caller who can reach the node's HTTP listener can retrieve any registered LOOP plugin's Prometheus metrics (and even its pprof profiling/debug data) by name.

### Finding Description
In `core/web/router.go`, `NewRouter` wires up route groups as follows: [1](#0-0) 

`debugRoutes`, `sessionRoutes`, and the `v2Routes` "authv2"/"userOrEI" groups all explicitly apply `auth.Authenticate(...)` middleware before exposing endpoints: [2](#0-1) [3](#0-2) 

By contrast, `loopRoutes` registers its handlers straight on the passed-in `r *gin.RouterGroup` with no auth middleware group at all: [4](#0-3) 

`pluginMetricHandler` in `core/web/loop_registry.go` looks up the plugin by the `:name` path param and proxies a GET request to the plugin's local Prometheus port, returning the raw response body to the caller with no authorization check on the caller's identity, role, or ownership of the plugin: [5](#0-4) 

The sibling handlers `pluginPPROFHandler` and `pluginPPROFPOSTSymbolHandler` have the exact same problem — they proxy to the plugin's `/debug/pprof/*` endpoints with no authentication: [6](#0-5) 

Since `api := engine.Group("/", rateLimiter(...), sessions.Sessions(...))` is a single shared group for the whole HTTP listener (no separate internal-only port or network binding is enforced in code), any client capable of reaching the node's exposed web server port — the same port used for the authenticated JSON API and GraphQL — can hit these plugin-proxy routes without any `Authorization` header or session cookie.

### Impact Explanation
This is a scoped information-disclosure vulnerability: an unauthenticated remote attacker can enumerate/guess LOOP plugin names and retrieve internal Prometheus metrics for plugins the attacker did not create or is not entitled to view, satisfying the "cross-tenant/cross-user response confusion" and "authorization exactness" criteria in the audit brief. The related pprof endpoints (`/plugins/:name/debug/pprof/*profile`, `/plugins/:name/debug/pprof/symbol`) are of higher concern since pprof can expose internal binary/runtime state (goroutine stacks, memory, symbol tables) that could aid further attacks against the plugin process, though this is a secondary/incidental finding on the same unauth code path.

### Likelihood Explanation
Preconditions are minimal to none: the attacker needs no credentials, no API token, no session cookie, and no elevated role — only network access to the node's HTTP port, which is the same port used to reach the public login/session endpoints. The exploit is trivially repeatable (a single unauthenticated GET request) and does not depend on any race condition, timing, or misconfiguration beyond the default routing wiring shown in `router.go`.

### Recommendation
Wrap `loopRoutes` registrations in an authenticated group, consistent with `debugRoutes`/`v2Routes`, e.g.:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
    loopRegistry := NewLoopRegistryServer(app)
    auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession, auth.AuthenticateByToken))
    auth.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
    auth.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
    auth.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
    auth.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
Additionally, restrict pprof forwarding to admin-role users given its sensitivity, and consider binding these internal-scrape endpoints to a separate, non-publicly-routable listener/port instead of the shared API `engine`.

### Proof of Concept
Go handler-level integration test plan (in `core/web` test package, mirroring existing router test patterns):
1. Build a minimal `chainlink.Application` mock/stub with `GetLoopRegistry()` returning a `*plugins.LoopRegistry` that has one plugin named `"victimPlugin"` registered (`Register`/equivalent from `plugins/loop_registry.go`), with `EnvCfg.PrometheusPort` pointing at a local `httptest.Server` that serves fake Prometheus text output.
2. Call `web.NewRouter(app, nil)` to obtain the `*gin.Engine`.
3. Issue `httptest.NewRequest("GET", "/plugins/victimPlugin/metrics", nil)` **without** setting any `Authorization` header or session cookie, and run it through `engine.ServeHTTP`.
4. Assert the response status is `200 OK` and the body matches the fake plugin's metrics payload — proving that metrics for a plugin the caller did not create/own are disclosed with zero authentication.
5. (Secondary) Repeat for `GET /plugins/victimPlugin/debug/pprof/heap` against a stub plugin pprof endpoint, asserting `200 OK` with no auth, to confirm the pprof proxy is equally exposed.
6. Contrast with a control request to an authenticated route, e.g. `GET /v2/keys/eth`, asserting `401`, to demonstrate the router's auth middleware works correctly elsewhere but is missing specifically for `loopRoutes`.

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

**File:** core/web/loop_registry.go (L150-188)
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
