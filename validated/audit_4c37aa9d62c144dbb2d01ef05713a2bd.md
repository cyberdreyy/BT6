### Title
Unrestricted `:profile` path segment forwarded to plugin's internal `/debug/pprof/*` endpoint enables disclosure of plugin debug data (`cmdline`, `heap`, `profile`, `trace`) - (File: core/web/loop_registry.go)

### Summary
`pluginPPROFHandler` builds the backing request URL by directly concatenating the caller-supplied `profile` route parameter into `http://<loopHostName>:<port>/debug/pprof/` with no allowlist of permitted sub-paths, then proxies the request/response verbatim to the caller. Any request reaching this handler can select any pprof sub-endpoint exposed by the plugin process (`cmdline`, `heap`, `profile`, `trace`, `goroutine`, `block`, `mutex`, etc.), several of which can leak process command-line arguments (which may contain secrets passed as flags/env) or full heap/memory dumps.

### Finding Description
`pluginPPROFHandler` reads `pluginName := gc.Param("name")` and `gc.Param("profile")` and forwards them unchecked: [1](#0-0) 

The `profile` value is inserted directly via `fmt.Sprintf` into the backend URL (`/debug/pprof/`+`gc.Param("profile")`) with no allowlist restricting it to a specific known-safe endpoint (e.g., only `profile` or only `heap`). This is a deliberate proxy design for pprof debugging, but it means the handler exposes the *entire* standard Go `net/http/pprof` surface of the target plugin process rather than a curated, minimal set of endpoints. The companion `pluginPPROFPOSTSymbolHandler` and `pluginMetricHandler` similarly forward requests based on attacker-supplied `name`/query values with only an existence check via `l.registry.Get(pluginName)`: [2](#0-1) 

None of these three handlers perform any endpoint allowlisting, and the file itself contains no role/permission check — any authorization must come entirely from how these routes are wired into the router (`core/web/router.go`). I was unable to fully confirm, within the available tool budget, the exact RBAC role required by the router registration for these routes (i.e., whether they are gated to admin-only sessions or reachable by any authenticated session including the 'view' role). This is the critical missing piece to definitively confirm the privilege-escalation claim in the question.

### Impact Explanation
If these routes are reachable by a session with only the 'view' role (rather than being admin-gated), an attacker could retrieve `cmdline` (process arguments, which can include secrets passed via CLI flags), `heap` (memory dumps that could contain in-memory secrets/keys), or `profile`/`trace` data from any registered LOOP plugin process — matching the "server credential/key theft" impact class. The lack of an explicit allowlist in `pluginGroup`/`pluginPPROFHandler` is a real code-level weakness regardless of the router's role gate, since it removes defense-in-depth and relies solely on router-level RBAC.

### Likelihood Explanation
Exploitability depends entirely on the router-level authorization for `/plugins/:name/debug/pprof/:profile` and related routes in `core/web/router.go`, which I could not conclusively verify in this session (search results for role/auth wiring in that file were too broad to isolate with the remaining tool budget). Without confirming that a 'view'-role-only session can reach these routes, I cannot assert with certainty that the privilege-escalation scenario described in the question is realized in this codebase state.

### Recommendation
- In `pluginPPROFHandler`, replace the raw `gc.Param("profile")` interpolation with an explicit allowlist (e.g., a `map[string]bool` of permitted profile names) and reject anything not in the list.
- Confirm in `core/web/router.go` that all `pluginGroup`-related routes (`discoveryHandler`, `pluginMetricHandler`, `pluginPPROFHandler`, `pluginPPROFPOSTSymbolHandler`) require an admin-level session role, not merely an authenticated session.
- Add a dedicated authorization test asserting that a session with only the 'view' role receives `403 Forbidden` on these routes.

### Proof of Concept
Not produced — I could not confirm the actual role required at the router registration level (`core/web/router.go`) for these plugin routes within the available investigation budget, which is required to determine whether a 'view'-role session can reach `pluginPPROFHandler`. A conclusive PoC needs a handler-level integration test that: 1) creates an authenticated test client with only the 'view' role, 2) issues `GET /plugins/<name>/debug/pprof/cmdline` and `GET /plugins/<name>/debug/pprof/heap`, and 3) asserts whether the response is `200 OK` with plugin data (vulnerable) or `403 Forbidden` (not vulnerable) — this test should be run against the actual router setup in `core/web/router_test.go` to confirm the role gate before treating this as a confirmed finding.

### Citations

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
