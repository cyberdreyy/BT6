### Title
Non-constant-time comparison of Prometheus metrics Bearer token enables timing side-channel token brute force - ([File: core/web/router.go])

### Finding Description
The `/metrics` endpoint handler `prometheusHandler` compares the client-supplied `Authorization` header directly against the expected `"Bearer " + token` value using Go's native string inequality operator: `if header != bearer` [1](#0-0) . Go's `!=` operator on strings performs a byte-by-byte comparison that returns as soon as a mismatching byte is found, rather than using a fixed-time comparison algorithm such as `crypto/subtle.ConstantTimeCompare`. This handler is wired up as the sole gate protecting the Prometheus metrics endpoint, registered via `e.GET(p.MetricsPath, prometheusHandler(p.Token, h))` in `prometheusUse` [2](#0-1) , and `prometheusUse` is invoked unconditionally when Prometheus metrics are enabled in `NewRouter` [3](#0-2) . An unauthenticated network attacker who can send HTTP requests to the metrics endpoint (no session, no API token, no prior authorization required) controls the full `Authorization` header value and can measure response latency per guessed prefix, potentially recovering the token byte-by-byte or char-by-char given enough samples, since a longer correct prefix causes marginally more comparison work before mismatch is detected.

### Impact Explanation
If successfully exploited, an attacker could recover the static Bearer token protecting `/metrics` and gain unauthorized read access to internal Prometheus counters/gauges exposed by the node (request rates, internal service metrics, potentially chain/job related counters). This is a low-to-moderate confidentiality impact scoped strictly to the metrics endpoint; it does not grant write access, fund movement, or job/key control.

### Likelihood Explanation
Exploitability in practice is low. The token comparison is a single short string compare, and any real timing signal from a byte-level mismatch (nanosecond-scale) is dwarfed by ordinary network jitter, TLS handshake overhead, Go runtime scheduling noise, and HTTP server/handler dispatch overhead. Extracting a usable timing signal over a real network (rather than a local loopback micro-benchmark) requires an extremely large number of samples and statistical control that is generally impractical for tokens of typical length/entropy used here. No rate limiting is specifically shown to gate `/metrics` requests (metrics route bypasses the authenticated API group's rate limiter since it's registered directly on the engine), which somewhat increases feasibility of high-volume timing sampling, but the signal-to-noise ratio for a single short compare remains the dominant limiting factor.

### Recommendation
Replace the direct string comparison with a constant-time comparison, e.g.:
```go
if subtle.ConstantTimeCompare([]byte(header), []byte(bearer)) != 1 {
    c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
    return
}
```
using `crypto/subtle.ConstantTimeCompare`, and ensure both compared byte slices are of consistent handling (hash-then-compare or fixed-length padding) to avoid leaking length information as well.

### Proof of Concept
Go handler-level test plan:
1. Construct `prometheusHandler(token, dummyHandler)` with a known long token (e.g., 64 hex chars).
2. Issue repeated HTTP requests (via `httptest.NewRecorder`/`ServeHTTP`) with `Authorization: Bearer <guess>` where `<guess>` shares an increasing correct prefix length (0, 10%, 50%, 90%, 100% correct prefix, then wrong suffix) vs. a completely wrong token of the same length.
3. Measure wall-clock latency of `prometheusHandler`'s comparison branch in isolation (bypassing network) over many thousands of iterations per prefix length, using `testing.B` or manual `time.Now()` deltas with outlier trimming/statistical averaging.
4. Assert: current implementation should show statistically significant (even if small) latency correlation with correct-prefix length using `!=`; after fix with `subtle.ConstantTimeCompare`, assert latency variance across prefix lengths is not statistically distinguishable (e.g., using a t-test or fixed epsilon threshold on mean latency difference).
5. Note in the PoC report that this in-process timing differential does not necessarily translate to a practically exploitable remote attack due to network noise, but demonstrates the underlying non-constant-time code path exists as flagged.

### Citations

**File:** core/web/router.go (L59-61)
```go
	if prometheus != nil {
		prometheusUse(prometheus, engine, promhttp.HandlerOpts{EnableOpenMetrics: true})
	}
```

**File:** core/web/router.go (L662-674)
```go
func prometheusUse(p *ginprom.Prometheus, e *gin.Engine, handlerOpts promhttp.HandlerOpts) {
	var (
		r prometheus.Registerer = p.Registry
		g prometheus.Gatherer   = p.Registry
	)
	if p.Registry == nil {
		r = prometheus.DefaultRegisterer
		g = prometheus.DefaultGatherer
	}
	h := promhttp.InstrumentMetricHandler(r, promhttp.HandlerFor(g, handlerOpts))
	e.GET(p.MetricsPath, prometheusHandler(p.Token, h))
	p.Engine = e
}
```

**File:** core/web/router.go (L691-696)
```go
		bearer := "Bearer " + token

		if header != bearer {
			c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
			return
		}
```
