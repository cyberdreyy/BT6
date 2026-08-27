Confirmed the vulnerable code path.

### Title
CORS wildcard suffix check missing dot boundary allows attacker-controlled origins to bypass allowlist - ([File: core/services/gateway/network/httpserver.go])

### Summary
The gateway's `isAllowedOrigin` function checks wildcard-configured allowed origins (`*.domain.com`) using `strings.HasSuffix(originHost, allowedHost)` after stripping the `*.` prefix, without verifying a dot boundary exists before the matched suffix. An attacker can register/control a domain like `evilremix.com` or `notremix.com` that is not a subdomain of `remix.com` but still passes the `HasSuffix` check when the gateway is configured with `CORSAllowedOrigins = ["https://*.remix.com"]`, causing the gateway to reflect the attacker's malicious `Origin` into `Access-Control-Allow-Origin` and grant cross-origin access.

### Finding Description
In `core/services/gateway/network/httpserver.go`, `handleRequest` (lines 180-187) reads the `Origin` request header from any unauthenticated client and calls `isAllowedOrigin` (line 142-178). When a configured allowed origin has the wildcard form `*.<suffix>` (line 170-171), the code strips the `*.` and does: [1](#0-0) 

```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if strings.HasSuffix(originHost, allowedHost) {
        return true
    }
}
```

`strings.HasSuffix` performs a raw string suffix comparison with no boundary check for a preceding `.` (or start-of-string) before the matched suffix. Given an operator configuration of `CORSAllowedOrigins = ["https://*.remix.com"]`, `allowedHost` becomes `remix.com`. An attacker-controlled origin `https://evilremix.com` has `originHost = "evilremix.com"`, and `strings.HasSuffix("evilremix.com", "remix.com")` evaluates to `true` because `"evilremix.com"` literally ends with the character sequence `"remix.com"` even though `evilremix.com` is an entirely different, attacker-registrable domain rather than a subdomain of `remix.com`. The same applies to `notremix.com`, `fakeremix.com`, etc. Since scheme and port checks (lines 158-164) do not constrain the host itself, this bypass is independent of them.

Once `isAllowedOrigin` returns `true`, `handleRequest` reflects the raw attacker `Origin` value verbatim into `Access-Control-Allow-Origin` (line 184), enabling the attacker's page (served from `evilremix.com`) to make cross-origin, CORS-permitted requests to the gateway's user-facing HTTP endpoint.

### Impact Explanation
This is a real allowlist bypass: the CORS policy is meant to isolate cross-origin browser access to only trusted origins configured by the node/gateway operator (e.g., `*.remix.com` for the Remix IDE integration). An attacker who registers a lookalike domain ending in the same character sequence (`evilremix.com`, `notremix.com`) can have their web page treated as a trusted origin, letting a victim's browser (if the victim visits the attacker's site while having relevant credentials/cookies or if the endpoint doesn't require auth but returns sensitive data) issue requests that the gateway will respond to with CORS headers permitting the attacker's origin to read the response. This matches the "allowlist/quota bypass" and "cross-user response confusion" impact classes since it undermines the CORS isolation invariant the operator configured.

### Likelihood Explanation
Exploitability requires only that: (1) the operator has `CORSEnabled = true` with at least one wildcard entry in `CORSAllowedOrigins` (a supported and documented configuration pattern, as seen in sample configs and tests using `*.ethereum.org`, `*.remix.com`-style wildcards), and (2) the attacker registers or controls a domain string ending in the same suffix. No authentication, special role, or privileged access is needed — any unauthenticated web attacker able to register a domain (e.g., `evilremix.com`) and lure a browser to send a request can trigger this. This is fully reproducible and deterministic given `strings.HasSuffix`'s well-defined semantics.

### Recommendation
Add a dot-boundary check when matching wildcard suffixes: require that `originHost` either equals `allowedHost` exactly or ends with `"." + allowedHost`, e.g.:
```go
if strings.HasPrefix(allowed, "*.") {
    suffix := allowedHost // already stripped of "*."
    if originHost == suffix || strings.HasSuffix(originHost, "."+suffix) {
        return true
    }
}
```
This ensures `evilremix.com` and `notremix.com` no longer match `*.remix.com`, while `sub.remix.com` continues to match.

### Proof of Concept
Add a Go unit test in `core/services/gateway/network/httpserver_test.go` (or a direct table test on `isAllowedOrigin` if exported/tested via reflection or a small wrapper) similar to the existing `TestHTTPServer_HandleRequest_CORSEnabled_FromNotAllowedOriginWildcards`:

```go
func TestHTTPServer_HandleRequest_CORSEnabled_WildcardBoundaryBypass(t *testing.T) {
    t.Parallel()
    _, handler, url := startNewServer(t, 100_000, 100_000, true,
        []string{"https://*.remix.com"})

    handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

    // Attacker-controlled origin that is NOT a subdomain of remix.com
    origin := "https://evilremix.com"
    resp, _ := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
    // EXPECTED (post-fix): Access-Control-Allow-Origin should be empty
    // ACTUAL (current bug): reflects "https://evilremix.com" due to missing dot boundary in HasSuffix
    require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"))

    // legitimate subdomain should still pass
    origin = "https://sub.remix.com"
    resp, _ = sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
    require.Equal(t, origin, resp.Header.Get("Access-Control-Allow-Origin"))
}
```
Running this against the current implementation demonstrates `evilremix.com` incorrectly receives `Access-Control-Allow-Origin: https://evilremix.com`, confirming the missing dot-boundary bug in the `HasSuffix(originHost, allowedHost)` check at [2](#0-1) .

### Citations

**File:** core/services/gateway/network/httpserver.go (L169-175)
```go
		// check for wildcard host match (e.g., *.remix.com)
		if strings.HasPrefix(allowedHost, "*.") {
			allowedHost = allowedHost[2:]
			if strings.HasSuffix(originHost, allowedHost) {
				return true
			}
		}
```
