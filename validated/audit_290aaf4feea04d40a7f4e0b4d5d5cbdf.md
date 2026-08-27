### Title
CORS wildcard origin check bypass via missing dot-boundary in suffix match - ([File: core/services/gateway/network/httpserver.go])

### Summary
The `isAllowedOrigin` function in `httpServer` performs wildcard CORS origin matching using `strings.HasSuffix(originHost, allowedHost)` without requiring a `.` boundary before the suffix. This allows a domain like `evilremix.com` to be treated as an allowed origin when the configured allowlist entry is `*.remix.com`, since `evilremix.com` naturally ends with `remix.com`.

### Finding Description
In `core/services/gateway/network/httpserver.go`, `isAllowedOrigin` handles wildcard entries as follows: [1](#0-0) 

When an allowlist entry starts with `*.` (e.g. `*.remix.com`), the code strips the `*.` prefix leaving `remix.com`, then checks `strings.HasSuffix(originHost, allowedHost)`. This check only verifies that `originHost` ends with the string `remix.com` — it does not require that the character preceding that suffix be a `.` (i.e., a proper subdomain boundary). Consequently, an attacker-registered domain such as `evilremix.com` satisfies `strings.HasSuffix("evilremix.com", "remix.com") == true`, even though `evilremix.com` is not a subdomain of `remix.com`.

This function is invoked directly from the gateway's request handler: [2](#0-1) 

When `CORSEnabled` is true and the incoming `Origin` header passes `isAllowedOrigin`, the server reflects the attacker-supplied `Origin` value verbatim into the `Access-Control-Allow-Origin` response header, granting the requesting page cross-origin access to the gateway API. Since the attacker fully controls the `Origin` header value (any domain they register/control, e.g. `evilremix.com`), and the check is purely string-based with no boundary enforcement, this is directly reachable by an unauthenticated browser-based attacker who lures a victim to a page hosted on `evilremix.com` and issues requests to the gateway.

### Impact Explanation
This is an authorization/allowlist bypass (AUTHORIZATION_EXACTNESS violation): an operator who intends to trust only `*.remix.com` subdomains inadvertently trusts any domain ending in that string without a dot separator (e.g., `evilremix.com`, `notremix.com`, `fakeremix.com`). Combined with CORS credentialed requests, this enables an unauthorized third-party origin to interact with the gateway API as if it were a trusted origin, which can facilitate CSRF-like abuse against the gateway from a malicious webpage.

### Likelihood Explanation
Exploitability requires only that the operator has configured a wildcard entry in `CORSAllowedOrigins` (e.g., `*.remix.com`) and that CORS is enabled — a realistic and common configuration. The attacker needs no credentials; they only need to register/control a domain that happens to end with the allowed suffix (e.g., `evilremix.com`) and host a malicious page there. This is fully attacker-controlled and repeatable.

### Recommendation
Fix the suffix check to require a dot boundary, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if originHost == allowedHost || strings.HasSuffix(originHost, "."+allowedHost) {
        return true
    }
}
```

### Proof of Concept
Add a Go unit test in `core/services/gateway/network/httpserver_test.go` (or new test file) that constructs an `httpServer` with `config.CORSAllowedOrigins = []string{"https://*.remix.com"}` and calls the unexported `isAllowedOrigin` method (test lives in same package `network`):
```go
func TestIsAllowedOrigin_WildcardBoundary(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.remix.com"}},
        lggr:   logger.Test(t),
    }
    // legit subdomain should pass
    assert.True(t, s.isAllowedOrigin("https://app.remix.com"))
    // malicious lookalike domain must NOT pass
    assert.False(t, s.isAllowedOrigin("https://evilremix.com"))
}
```
Currently `isAllowedOrigin("https://evilremix.com")` returns `true`, demonstrating the bug; after applying the recommended fix it should return `false`.

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
