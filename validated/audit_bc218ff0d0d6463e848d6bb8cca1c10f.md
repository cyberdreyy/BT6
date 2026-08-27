This confirms a genuine finding, though the file path cited in the question (`core/services/gateway/handlers/common/message_util.go`) is unrelated — the actual vulnerable code is in `core/web/router.go`, not the gateway package. I'll answer based on the real, verifiable code path.

### Title
Unauthenticated access to LOOP plugin metrics/pprof endpoints due to missing auth middleware in `loopRoutes` - ([File: core/web/router.go])

### Summary
`NewRouter` registers `loopRoutes(app, api)` directly on the shared `api` route group, which only carries a rate limiter and session-cookie-parsing middleware, unlike `debugRoutes`, `sessionRoutes`, and `v2Routes` which explicitly wrap their sub-groups with `auth.Authenticate(...)`. As a result, `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` are reachable by any unauthenticated client.

### Finding Description
In `NewRouter`, the `api` group is created with only rate limiting and session middleware: [1](#0-0) . Compare this with `debugRoutes`, which creates its own sub-group and explicitly requires `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` before exposing `/debug/vars`: [2](#0-1) , and with `v2Routes`, which splits routes into an `unauthedv2` group and an `authv2` group wrapped in `auth.Authenticate(...)`: [3](#0-2) , and `sessionRoutes`, which similarly separates unauthenticated and authenticated sub-groups: [4](#0-3) .

`loopRoutes`, however, registers its handlers directly on the passed-in `r` (`api`) group with no additional `auth.Authenticate` wrapper at all: [5](#0-4) . This means these four routes inherit only the outer middleware stack (rate limiter + session cookie parsing) applied at `engine.Group("/")`, but never pass through any authentication check.

The handlers exposed are: `discoveryHandler` (lists all registered LOOP plugin names and their Prometheus ports/hostnames) [6](#0-5) ; `pluginMetricHandler`, which proxies to `http://<loopHostName>:<PrometheusPort>/metrics` for a plugin named via `:name` [7](#0-6) ; and `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler`, which proxy raw `net/http/pprof` requests (including profile capture and symbol lookups) to the plugin's internal HTTP endpoint [8](#0-7) .

An unauthenticated attacker with network access to the node's web server can call these endpoints directly (e.g., `GET /discovery`, `GET /plugins/<name>/metrics`, `GET /plugins/<name>/debug/pprof/profile?seconds=30`) with no session cookie or API token, since no `auth.Authenticate` middleware sits in the handler chain for this route group.

### Impact Explanation
This is an authentication-bypass exposing internal plugin telemetry and debug internals: plugin names, their Prometheus metrics (which may contain sensitive operational/business data about the running LOOP plugins, e.g., chain configuration counts, job execution rates), and full Go pprof profiling data (goroutine stacks, heap dumps, CPU profiles) which can leak internal state, memory contents, and structural information useful for further attacks. `pluginPPROFHandler` also allows attacker-controlled `seconds` parameter to trigger CPU profiling/blocking captures on the LOOP plugin process, which could be used as a resource-exhaustion vector by an unauthenticated caller. This matches the Chainlink bounty class of authentication bypass / unauthorized information disclosure of internal node data.

### Likelihood Explanation
Feasible and repeatable with zero credentials: the attacker only needs network reachability to the node's configured web server port (same precondition as any other unauthenticated node API endpoint). No role, API token, or session cookie is required, since no `auth.Authenticate` call appears anywhere in `loopRoutes` or its callees. This is trivially reproducible by any HTTP client.

### Recommendation
Wrap the `loopRoutes` handlers in an authenticated sub-group, consistent with `debugRoutes`, e.g.:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	group := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	group.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	group.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	group.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
If `/discovery` must remain reachable by an external, unauthenticated Prometheus scraper by design, that should be an explicit, documented decision with network-level (not code-level) mitigations — but the pprof and metrics-proxy endpoints in particular should require authentication.

### Proof of Concept
Go handler-level integration test plan:
1. Build a minimal `chainlink.Application` mock/fixture sufficient for `web.NewRouter` (as done in existing router tests), with `AuthenticationProvider()` and `GetLoopRegistry()` returning a registry containing one dummy plugin (e.g., name `"median"`, `EnvCfg.PrometheusPort` set to a local test HTTP server port).
2. Call `web.NewRouter(app, nil)` to get the `*gin.Engine`.
3. Using `httptest.NewServer(engine)` (no cookies/tokens attached), issue:
   - `GET /discovery` → assert `200 OK` and JSON body listing the dummy plugin (proves no auth needed, unlike e.g. `GET /v2/config` which should return `401`).
   - `GET /plugins/median/metrics` → assert `200 OK` proxied content, with no `Authorization` header or session cookie set.
   - `GET /plugins/median/debug/pprof/goroutine` → assert `200 OK`.
4. As a control, issue `GET /v2/config` and `GET /debug/vars` with no auth and assert `401 Unauthorized`, demonstrating the asymmetry between `loopRoutes` and the other route groups (`v2Routes`, `debugRoutes`) that correctly enforce `auth.Authenticate`.
5. Optionally, enumerate `engine.Routes()` and, for each route under `/discovery` or `/plugins/`, assert (via reflection on registered `HandlerFunc`s or by comparing function pointers) that none match `auth.Authenticate(...)`'s returned closure, while routes under `/v2` and `/debug` do.

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

**File:** core/web/router.go (L207-218)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
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

**File:** core/web/loop_registry.go (L52-81)
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

	b, err := l.jsonMarshalFn(groups)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, err = w.Write([]byte(err.Error()))
		if err != nil {
			l.logger.Error(err)
		}
		return
	}
	_, err = w.Write(b)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		l.logger.Error(err)
	}
}
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

**File:** core/web/loop_registry.go (L150-215)
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
