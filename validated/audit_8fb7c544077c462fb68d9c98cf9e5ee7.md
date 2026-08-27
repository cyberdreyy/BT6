### Title
CORS wildcard origin bypass via missing dot-boundary check in `isAllowedOrigin` - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` matches wildcard allowed origins (e.g. `*.ethereum.org`) using `strings.HasSuffix(originHost, allowedHost)` without verifying a `.` boundary before the suffix. An attacker who controls a domain such as `evilethereum.org` (no dot separating "evil" from "ethereum.org") satisfies the suffix check and is treated as a valid subdomain of the allowlisted domain.

### Finding Description
In `core/services/gateway/network/httpserver.go`, the wildcard-matching branch is: [1](#0-0) 

`allowedHost` is stripped of its `*.` prefix (e.g. `ethereum.org`), and then `strings.HasSuffix(originHost, allowedHost)` is used directly. This check has no requirement that the character immediately preceding the matched suffix in `originHost` be a `.` (i.e., a real subdomain boundary). Consequently, `originHost = "evilethereum.org"` (or `"evil-ethereum.org"` won't match since it lacks a dot, but any domain literally ending in the byte sequence `ethereum.org`, such as `notethereum.org` or `xethereum.org`) will incorrectly satisfy `HasSuffix`.

This function is invoked directly from the request handler for every incoming request when CORS is enabled: [2](#0-1) 

If `isAllowedOrigin` returns true, the server echoes the attacker-controlled `Origin` header value back as `Access-Control-Allow-Origin`, which the browser will honor for that specific attacker-registered domain, allowing the attacker's page to read cross-origin responses from the gateway in the victim's browser context (subject to the browser also sending any implicit credentials, e.g. via `fetch(..., {credentials: 'include'})` if applicable to the deployment).

No other validation (auth middleware, allowlist normalization, or dot-boundary check) exists before or after this point in the CORS handling path.

### Impact Explanation
This is a CORS allowlist bypass: an attacker registering an arbitrary domain ending in the same string as an allowlisted apex domain (no dot boundary required) can have their origin whitelisted by the gateway's CORS policy. This enables cross-origin browser reads of gateway responses that operators intended to restrict to trusted subdomains of the allowlisted domain, matching the "allowlist bypass" impact class in Chainlink's bounty criteria for the gateway service.

### Likelihood Explanation
No credentials or privileged access are needed — the attacker only needs to register/control any domain matching the byte-suffix pattern (e.g. `evilethereum.org`) and lure a victim's browser to a page on that origin, then issue a cross-origin request to the gateway. This is fully attacker-controlled and repeatable, requiring only that the operator configured a wildcard entry (e.g. `https://*.ethereum.org`) in `CORSAllowedOrigins`.

### Recommendation
Enforce a proper subdomain boundary check in the wildcard branch, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if originHost == allowedHost || strings.HasSuffix(originHost, "."+allowedHost) {
        return true
    }
}
```
This guarantees the matched suffix is preceded by a literal `.` label separator, preventing sibling-string bypasses like `evilethereum.org`.

### Proof of Concept
Add a table-driven Go unit test for `isAllowedOrigin` in `core/services/gateway/network`:
```go
func TestIsAllowedOrigin_WildcardBoundary(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.ethereum.org"}},
        lggr:   logger.Test(t),
    }
    assert.True(t, s.isAllowedOrigin("https://sub.ethereum.org"))   // legitimate subdomain
    assert.False(t, s.isAllowedOrigin("https://evilethereum.org"))  // attacker bypass - currently returns true (bug)
}
```
Running against current code, the second assertion fails (returns `true`), confirming the bypass; after applying the recommended fix, it returns `false` as expected.

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

**File:** core/services/gateway/network/httpserver.go (L180-187)
```go
func (s *httpServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.config.CORSEnabled {
		origin := r.Header.Get("Origin")
		if s.isAllowedOrigin(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}
```
