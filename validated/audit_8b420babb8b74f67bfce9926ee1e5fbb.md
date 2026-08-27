### Title
Unauthenticated LOOP plugin pprof heap-dump proxy exposes in-memory secrets - (File: core/web/loop_registry.go)

### Finding Description
`pluginPPROFHandler` resolves `gc.Param("name")` against `l.registry.Get(pluginName)` and, if found, builds `pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/"+gc.Param("profile"), l.loopHostName, p.EnvCfg.PrometheusPort)` and proxies it verbatim via `doRequest`, writing the raw response body back with `gc.Data(http.StatusOK, "text/plain", b)` [1](#0-0) [2](#0-1) . Neither this handler nor `doRequest` performs any authentication, session, or role check, and neither redacts or filters the proxied response — the plugin's `/debug/pprof/heap` (or `profile`, `goroutine`, `trace`, etc.) output is returned to the caller byte-for-byte. Plugin names are enumerable from the unauthenticated `discoveryHandler`, which lists every registered LOOP plugin name in its JSON response with no auth gate of its own [3](#0-2) . A Go heap profile can contain live in-memory byte slices, including key material, session/API tokens, or other sensitive state held by the LOOP plugin process, so proxying it verbatim to any caller who can reach these routes violates the "secrets never leave the process" design goal for LOOP plugins.

### Impact Explanation
If these routes are reachable without session/auth middleware (which the code comments confirm is the intent — the discovery/metrics/pprof surface is designed for external Prometheus scraping, which typically doesn't authenticate the way the JSON-RPC/GraphQL API does), an attacker can dump the full in-process heap of any registered LOOP plugin and search it for private key bytes, CSA/OCR key material, or bearer tokens. This is a critical information-disclosure vector matching Chainlink's "secrets/key disclosure" bounty impact class, since it does not require compromising any account — merely network access to the port serving `/plugins/:name/debug/pprof/*`.

### Likelihood Explanation
No credentials, session, or role are needed to reach `pluginPPROFHandler`: the plugin name is obtainable from the equally-unauthenticated `/discovery` endpoint, and the handler forwards the request as-is with no additional checks in `pluginPPROFHandler`/`doRequest`. Exploitability depends only on the attacker being able to route a GET request to the exposed `plugins/:name/debug/pprof/heap` path on the same listener that serves discovery/metrics, which is fully repeatable and requires no state or race condition.

### Recommendation
Require the same authenticated/admin session middleware used for the rest of the node's sensitive routes on all `/plugins/:name/debug/pprof/*` and `/discovery` endpoints, or move pprof/metrics endpoints to a separate internal-only listener bound to loopback/private interfaces, gated by a distinct auth token. At minimum, disable `heap`/`profile`/`trace`/`goroutine` pprof forwarding by default and require an explicit operator opt-in plus authentication before proxying plugin process memory dumps.

### Proof of Concept
1. In a Go test (mirroring `core/web/loop_registry_internal_test.go` style), register a fake `plugins.RegisteredLoop` in a `plugins.LoopRegistry` whose name is `"victim-plugin"`.
2. Start an `httptest.Server` acting as the stub LOOP process; have it serve `/debug/pprof/heap` returning a fixed byte payload embedding a marker string simulating key material (e.g., `"FAKE_PRIVATE_KEY_BYTES_1234"`).
3. Set `loopHostName`/`PrometheusPort` on the `LoopRegistryServer` (or `EnvCfg.PrometheusPort`) to point at the stub server's port.
4. Construct a `gin.Context` with `Param("name") = "victim-plugin"`, `Param("profile") = "heap"`, and no `Authorization`/session cookie set.
5. Call `l.pluginPPROFHandler(gc)` directly (or through the full router if middleware wiring is under test).
6. Assert: response status is `200`, and response body equals the stub server's payload verbatim, including the marker string — proving no redaction, filtering, or auth check occurred before the sensitive bytes were returned to the caller.

### Citations

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
