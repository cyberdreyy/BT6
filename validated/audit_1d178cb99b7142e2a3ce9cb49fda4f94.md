### Title
Unauthenticated LOOP plugin pprof endpoint discloses in-memory secrets via heap/goroutine dumps - (File: core/web/loop_registry.go, core/web/router.go)

### Summary
The route `GET /plugins/:name/debug/pprof/*profile` is registered in `loopRoutes` directly on the base `api` router group without any `auth.Authenticate` wrapper, unlike every other sensitive route in `NewRouter` (e.g. `sessionRoutes`, `v2Routes`). This lets any unauthenticated caller pull full pprof profiles (heap, goroutine, `debug=2` source dumps) from a running LOOP plugin process.

### Finding Description
`NewRouter` builds an `api` group with only rate limiting and session middleware, then calls `loopRoutes(app, api)`, `sessionRoutes`, `v2Routes`, etc. [1](#0-0) 

Unlike `sessionRoutes` and `v2Routes`, which explicitly create a sub-group wrapped in `auth.Authenticate(...)` for anything sensitive, `loopRoutes` registers its handlers straight on `r` with no auth wrapper at all: [2](#0-1) 

The handler itself, `pluginPPROFHandler`, looks up the plugin by name from the registry, builds the forwarding URL `http://<loopHostName>:<PrometheusPort>/debug/pprof/<profile>` (including `debug`, `gc`, `seconds` query params), and forwards the request body/response as-is with no authentication or authorization check: [3](#0-2) 
The proxy call `doRequest` simply forwards the response bytes back to the caller with `gc.Data(http.StatusOK, ...)`, without redaction: [4](#0-3) 

Plugin names are enumerable via the unauthenticated `/discovery` endpoint, which lists all registered LOOP plugins: [5](#0-4) 

There is no auth.Authenticate, session check, or role check anywhere in this chain (`api` group -> `loopRoutes` -> `pluginPPROFHandler`), so an attacker only needs network access to the node's web server and a plugin name obtained from `/discovery`.

### Impact Explanation
`net/http/pprof` heap and goroutine dumps (especially with `debug=2`) can contain raw in-memory data including private keys, OCR/session secrets, API tokens, and other sensitive state held by the LOOP plugin process. Exposing this to any unauthenticated caller is a credential/secret disclosure vulnerability that can lead to full compromise of the plugin's cryptographic material and, transitively, of the node's operational security.

### Likelihood Explanation
No credentials, roles, or special network position are required — a plain unauthenticated HTTP GET is sufficient. The plugin name is discoverable via the also-unauthenticated `/discovery` route, or simply guessable from known plugin identifiers (e.g. `median`, `mercury`, etc.). This is trivially repeatable and requires no timing or race conditions.

### Recommendation
Wrap `loopRoutes` (and likely `pluginMetricHandler`, which has the same issue) in an authenticated sub-group, e.g.:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
    loopRegistry := NewLoopRegistryServer(app)
    authed := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))
    authed.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
    authed.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
    authed.GET("/plugins/:name/debug/pprof/*profile", auth.RequiresAdminRole(loopRegistry.pluginPPROFHandler))
    authed.POST("/plugins/:name/debug/pprof/symbol", auth.RequiresAdminRole(loopRegistry.pluginPPROFPOSTSymbolHandler))
}
```
At minimum the pprof endpoints should require admin-role authentication, since they expose highly sensitive process internals.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/loop_registry_test.go`):
1. Start `cltest.NewApplicationWithConfigAndKey` as in `TestLoopRegistry`, register a mock loop plugin, and start the real gin router via `NewRouter`/`app.NewHTTPClient`.
2. Using `client := app.NewHTTPClient(nil)` (an unauthenticated client, or explicitly construct an `http.Client` with no session cookie / API token), issue:
   - `GET /discovery` — assert `200 OK` and that the plugin name is present in the response, proving enumerability with zero credentials.
   - `GET /plugins/<pluginName>/debug/pprof/heap?debug=2` with no `Cookie`/`Authorization` header set — assert `200 OK` (not `401`/`403`) and that the response body contains recognizable pprof heap-dump content (e.g. `runtime.MemStats` markers or Go symbol names).
   - `GET /plugins/<pluginName>/debug/pprof/goroutine?debug=2` — same assertion.
3. Contrast with an authenticated-only endpoint like `DELETE /sessions` or `/v2/users`, confirming those return `401 Unauthorized` without a session, while the pprof route returns `200 OK` under identical unauthenticated conditions — demonstrating the missing `auth.Authenticate` wrapper specifically on `loopRoutes`.

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
