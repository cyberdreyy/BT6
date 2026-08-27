### Title
Unauthenticated exposure of internal LOOP plugin Prometheus metrics via `/plugins/:name/metrics` - ([File: core/web/loop_registry.go])

### Summary
The `loopRoutes` registration in `core/web/router.go` mounts `LoopRegistryServer.pluginMetricHandler` (and the discovery/pprof handlers) on the shared `api` route group without wrapping them in any `auth.Authenticate*` middleware, unlike every other sensitive route group (`v2Routes`, `debugRoutes`, `sessionRoutes`). Any unauthenticated caller can therefore hit `GET /plugins/:name/metrics` for any registered plugin and receive the raw Prometheus metrics text scraped from the internal LOOP process.

### Finding Description
`loopRoutes` is invoked directly on the bare `api` group: [1](#0-0) . The `api` group itself is only wrapped with a rate limiter and gin session middleware, not an authentication requirement: [2](#0-1) . Compare this to `debugRoutes`, which explicitly wraps its group with `auth.Authenticate(...)`: [3](#0-2) , and `v2Routes`, which uses `authv2 := r.Group("/v2", auth.Authenticate(...))` for all sensitive endpoints including the authenticated `metricRoutes(authv2)` pprof group: [4](#0-3) [5](#0-4) . The `loopRoutes` function itself registers `/discovery`, `/plugins/:name/metrics`, and the pprof forwarding endpoints with no auth middleware at all: [6](#0-5) .

Inside `pluginMetricHandler`, `l.registry.Get(pluginName)` only validates that the plugin name exists in the registry — it performs no session/token/role checks — and then proxies the request to the internal LOOP process's `/metrics` endpoint: [7](#0-6) . The pprof forwarding handlers (`pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`) share the same unauthenticated exposure, allowing anonymous callers to also pull heap/goroutine/profile/trace data and even POST to `/debug/pprof/symbol` on the internal plugin process: [8](#0-7) .

The existing test `TestLoopRegistry` confirms this behavior is currently by design/unenforced: it uses `app.NewHTTPClient(nil)` (no credentials) and asserts `http.StatusOK` for `/discovery`, `/plugins/mockLoopImpl/metrics`, and `/metrics`: [9](#0-8) .

### Impact Explanation
Prometheus metrics scraped from LOOP plugins can include operational details such as per-job/per-request labels, internal identifiers, request counts, latencies, and other instrumentation that plugin authors may embed as label values. This falls under Chainlink's "sensitive information disclosure" impact class — an anonymous, non-privileged actor with no operator credentials can enumerate and read internal telemetry data intended to be scraped by a trusted, network-restricted Prometheus instance (per the code comment "unlike discovery, this endpoint is internal btw the node and plugin"), not exposed to arbitrary external HTTP clients.

### Likelihood Explanation
Likelihood is high and requires zero attacker privilege: no authentication, session, or API token is needed since the `api` group and `loopRoutes` apply no `auth.Authenticate*` middleware. The only precondition is that at least one LOOP plugin is registered (a normal operational state for nodes running LOOP-based plugins, e.g. OCR2/median LOOPs), which is common in production deployments. The attacker only needs to know or guess a valid plugin name, which can often be inferred from public job/plugin naming conventions or from the also-unauthenticated `/discovery` endpoint that lists all registered plugin names.

### Recommendation
Wrap `loopRoutes` (or at minimum the `/plugins/:name/metrics` and pprof forwarding routes) in an authenticated route group, mirroring the pattern used for `debugRoutes` and the authenticated `metricRoutes(authv2)`:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
    loopRegistry := NewLoopRegistryServer(app)
    group := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateByToken, auth.AuthenticateBySession))
    group.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
    group.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
    group.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
    group.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
If these endpoints must remain reachable by an external, unauthenticated Prometheus scraper (as the `/discovery` service-discovery use case implies), gate them behind a separate bearer-token check similar to `prometheusHandler`'s token comparison, rather than leaving them open to any anonymous caller, and restrict pprof forwarding (which allows arbitrary internal debug data retrieval and a POST-based request) to authenticated admin/edit-role users only.

### Proof of Concept
Add a new test in `core/web/loop_registry_test.go` (or a table-driven variant of `TestLoopRegistry`) that:
1. Boots the app as in `TestLoopRegistry` and registers a mock LOOP plugin (`app.GetLoopRegistry().Register("mockLoopImpl")`), starting the mock Prometheus HTTP server as `mockLoopImpl` does.
2. Uses `client := app.NewHTTPClient(nil)` (unauthenticated, as today) to `GET /plugins/mockLoopImpl/metrics`.
3. Assert `resp.StatusCode == http.StatusUnauthorized` (or `http.StatusForbidden`) instead of the current `http.StatusOK`, i.e. the inverse of the existing "plugin metrics OK" subtest at [10](#0-9) , to demonstrate that after the fix, an anonymous client can no longer retrieve plugin metrics.
4. Add a second authenticated case using `app.NewHTTPClient(cltest.APIKeyValidRole)` (or the equivalent authenticated session helper used elsewhere, e.g. as in `core/web/eth_keys_controller_test.go`) and assert it still receives `http.StatusOK` with the expected metric body, confirming legitimate operator access is preserved.

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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L96-127)
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

**File:** core/web/loop_registry_test.go (L99-152)
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

	t.Run("plugin metrics OK", func(t *testing.T) {
		t.Parallel()
		// plugin name `mockLoopImpl` matches key in PluginConfigs
		resp, cleanup := client.Get(expectedLooppEndPoint)
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("plugin metrics response %s", b)

		var (
			exceptedCount  = 1
			expectedMetric = fmt.Sprintf("%s %d", testMetricName, exceptedCount)
		)
		require.Contains(t, string(b), expectedMetric)
	})

	t.Run("core metrics OK", func(t *testing.T) {
		t.Parallel()
		// core node metrics endpoint
		resp, cleanup := client.Get(expectedCoreEndPoint)
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("core metrics response %s", b)
	})
```
