### Title
Unauthenticated disclosure of internal plugin topology via `GET /discovery` and unauthenticated pprof/metrics proxying for LOOP plugins - (File: core/web/router.go)

### Summary
`loopRoutes` in `core/web/router.go` mounts `/discovery`, `/plugins/:name/metrics`, and `/plugins/:name/debug/pprof/*` directly on the base `api` router group with no `auth.Authenticate` wrapper, unlike every other sensitive route group (`debugRoutes`, `sessionRoutes`, `v2Routes`). This lets any unauthenticated network client enumerate internal LOOP plugin names/ports and pivot into unauthenticated pprof debug data proxied from the plugin processes.

### Finding Description
In `core/web/router.go`, the route groups are set up as: [1](#0-0) 
`debugRoutes` explicitly wraps its group with `auth.Authenticate(...)`: [2](#0-1) 
`sessionRoutes` and `v2Routes` similarly gate mutating/sensitive endpoints behind `auth.Authenticate` groups (`auth`, `authv2`, `userOrEI`): [3](#0-2) 

By contrast, `loopRoutes` registers its handlers straight onto the `r` (the top-level `api` group with only rate-limiting and session middleware, no auth check) with no `auth.Authenticate` call at all: [4](#0-3) 

The handler `discoveryHandler` in `core/web/loop_registry.go` returns Prometheus service-discovery JSON listing every registered LOOP plugin name, the discovery hostname, and the exposed Prometheus port: [5](#0-4) 

The companion `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` proxy requests straight to the internal plugin's `/metrics` and `/debug/pprof/*` endpoints using only the `:name` URL parameter, with no authentication check on the incoming gin request: [6](#0-5) [7](#0-6) 

The existing test `TestLoopRegistry` demonstrates the endpoint is reachable with a plain HTTP client with no credentials (`app.NewHTTPClient(nil)`), confirming no auth is required to hit `/discovery` and the plugin metrics endpoints: [8](#0-7) 

### Impact Explanation
An unauthenticated network client can call `GET /discovery` and learn every LOOP plugin's name, the exposed Prometheus port, and the discovery hostname, plus fetch `/plugins/:name/metrics` and forward requests into `/plugins/:name/debug/pprof/*` (heap, goroutine, profile, trace, symbol) of the plugin process. pprof heap/goroutine dumps can leak in-memory secret material (e.g., private key bytes, OCR key state) held by the LOOP plugin process, and the reconnaissance from `/discovery` narrows the attack surface for targeting specific plugins/ports. This falls into the "sensitive information disclosure" / reconnaissance-enabling-further-compromise class relevant to Chainlink's bounty program, though it does not by itself allow fund movement or job execution.

### Likelihood Explanation
No credentials, session, API token, or EI signature are required — the endpoints are reachable to any client that can send an HTTP request to the node's web server port, subject only to the generic rate limiter applied to the whole `api` group. This is fully reproducible and repeatable with a plain `curl`/`http.Client` GET request.

### Recommendation
Wrap `loopRoutes` in an authenticated group (mirroring `debugRoutes`), e.g. `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))` or gate it behind an admin/operator role, and/or restrict `discoveryHandler`/plugin metrics-pprof proxy endpoints to a separate internal-only listener not exposed on the public API port, consistent with the code's own comment that these are meant to be "internal btw the node and plugin."

### Proof of Concept
1. In a `core/web` router test (similar to `TestLoopRegistry`), build the app/router with `NewRouter` and register a mock LOOP plugin via `app.GetLoopRegistry().Register(...)`.
2. Using `httptest.NewRecorder()` and a request with **no** `Authorization` header, no session cookie, and no EI signature headers, issue `GET /discovery` against the `gin.Engine`.
3. Assert `http.StatusOK` and that the JSON body (`[]*targetgroup.Group`) contains the plugin's name via `web.LabelMetaPluginName` and the exposed port/hostname.
4. Repeat for `GET /plugins/<name>/metrics` and `GET /plugins/<name>/debug/pprof/heap` with zero credentials, asserting `200 OK` and non-empty body, confirming unauthenticated access and forwarding to the plugin's internal debug endpoints.

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

**File:** core/web/loop_registry_test.go (L99-122)
```go
	client := app.NewHTTPClient(nil)

	t.Run("discovery endpoint", func(t *testing.T) {
		t.Parallel()
		// under the covers this is routing thru the app into loop registry
		resp, cleanup := client.Get("/discovery")
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("discovery response %s", b)
		var got []*targetgroup.Group
		require.NoError(t, json.Unmarshal(b, &got))

		gotLabels := make([]model.LabelSet, 0, len(got))
		for _, ls := range got {
			gotLabels = append(gotLabels, ls.Labels)
		}
		assert.Len(t, gotLabels, len(expectedLabels))
		for i := range expectedLabels {
			assert.Equal(t, expectedLabels[i], gotLabels[i])
		}
	})
```
