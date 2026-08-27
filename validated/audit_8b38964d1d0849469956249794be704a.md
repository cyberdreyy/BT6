### Title
Unauthenticated pprof proxy endpoint allows attacker-controlled unbounded request timeout, enabling resource-exhaustion DoS - ([File: core/web/loop_registry.go])

### Summary
The `/plugins/:name/debug/pprof/*profile` route, served by `LoopRegistryServer.pluginPPROFHandler`, is registered on the node's main gin engine outside the authenticated session route group, and forwards the client's `?seconds=` query parameter directly into `pprofURLVals` to compute the outbound proxy request's context timeout with no upper bound. An unauthenticated attacker can request a very large `seconds` value repeatedly and hold open many concurrent long-lived server-side goroutines/connections.

### Finding Description
`pluginPPROFHandler` (core/web/loop_registry.go:150-166) resolves the plugin by name, builds the pprof URL, and calls `pprofURLVals(gc)` to build both the forwarded query string and the local timeout: [1](#0-0) . When the client supplies `seconds`, that raw integer is used both as the forwarded value and to compute `timeout = time.Duration(i+PPROFOverheadSeconds) * time.Second` with no minimum/maximum clamp. `doRequest` then creates a context with that attacker-controlled timeout and performs a blocking `http.DefaultClient.Do(req)` for its duration: [2](#0-1) . Because `exposedPromPort` is configured to the node's main `HTTPPort()` (`core/web/loop_registry.go:42`), and the plugin routes are wired directly on the router without going through the authenticated/session middleware group applied to other admin endpoints, any unauthenticated client of the node's public HTTP port can hit this handler and set an arbitrarily large `seconds` value, holding open a goroutine, an outbound TCP connection to the LOOP plugin, and the underlying gin request/response cycle for the full duration. Repeating this concurrently accumulates open goroutines/connections without any per-request cap, since there is no clamp on `seconds` and no authorization check gating the endpoint the way session-authenticated admin routes are gated.

### Impact Explanation
This matches Chainlink's "mutate node availability state" / resource-exhaustion impact class: sustained concurrent requests with large `seconds` values can exhaust the node's available goroutines/file descriptors/HTTP client connections, degrading or denying availability of the node's HTTP API for legitimate operators and other integrations, without requiring any credentials.

### Likelihood Explanation
No preconditions are required beyond network reachability to the node's HTTP port and knowledge of (or brute-forcing) a valid plugin name; the attack is trivially repeatable and can be parallelized cheaply by the attacker (`N` concurrent GETs), while the defender's cost scales with `N × seconds`.

### Recommendation
Gate `/plugins/:name/debug/pprof/*` and the `:name/debug/pprof/symbol` route behind the same session/authentication middleware used for other admin-sensitive endpoints, and separately clamp the `seconds` query parameter to a small, fixed maximum (e.g. 30-60s) in `pprofURLVals` regardless of authentication status, rejecting or truncating out-of-range values before they reach `doRequest`'s context timeout.

### Proof of Concept
1. Unit test on `pprofURLVals`: construct a `gin.Context` with query `seconds=3600`, assert returned `timeout == 3630*time.Second` and that no clamping occurs, demonstrating attacker control over the timeout with no upper bound.
2. Handler-level integration test: stand up a `LoopRegistryServer` wired to a stub LOOP HTTP server, issue `N` (e.g. 50) concurrent `GET /plugins/<name>/debug/pprof/profile?seconds=3600` without any `Authorization`/session cookie, and assert (a) the requests succeed (200) demonstrating no auth check, and (b) `runtime.NumGoroutine()` / open connection count grows and remains elevated for close to the full timeout window, confirming the resource-hold behavior.

### Citations

**File:** core/web/loop_registry.go (L132-148)
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
```

**File:** core/web/loop_registry.go (L190-205)
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
```
