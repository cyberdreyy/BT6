### Title
CORS wildcard origin check bypass via suffix-without-dot-boundary match in `isAllowedOrigin` - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` implements wildcard origin matching (`*.allowed.com`) by stripping the `*.` prefix and calling `strings.HasSuffix(originHost, allowedHost)`, which does not enforce a `.` boundary before the suffix. An attacker who registers a domain that merely ends with the allowed suffix (e.g. `evilallowed.com` for an allowed wildcard `*.allowed.com`) is incorrectly treated as a subdomain and granted CORS access.

### Finding Description
In `core/services/gateway/network/httpserver.go`, `handleRequest` reads the `Origin` request header and calls `s.isAllowedOrigin(origin)` [1](#0-0) . Inside `isAllowedOrigin`, exact matches are checked first, then wildcard matches are performed by stripping the `*.` prefix off the configured allowed host and testing `strings.HasSuffix(originHost, allowedHost)` [2](#0-1) .

`strings.HasSuffix` is a pure string-suffix check with no separator/boundary enforcement. If the operator configures `CORSAllowedOrigins` with a wildcard entry like `*.allowed.com`, the stripped comparison string becomes `allowed.com`. An attacker-controlled origin host `evilallowed.com` (which the attacker fully controls as a registered domain, not a subdomain of `allowed.com`) also satisfies `strings.HasSuffix("evilallowed.com", "allowed.com") == true`, because "evilallowed.com" ends with the exact character sequence "allowed.com" with no dot in between required by the check. The function therefore returns `true` and `handleRequest` reflects that attacker origin back via `Access-Control-Allow-Origin: <origin>` [3](#0-2) .

An attacker who registers such a domain (e.g., `evilallowed.com`, `notxallowed.com`) and serves a malicious webpage from it can issue cross-origin `fetch`/`XHR` requests to the gateway HTTP endpoint from a victim's browser; the browser will permit reading the response because the server's CORS headers approve the origin, even though the operator never intended to allow that domain.

### Impact Explanation
This allows an attacker-controlled origin to bypass the CORS allowlist intended to restrict which web origins can read gateway HTTP responses (cross-user data exfiltration / origin allowlist bypass), matching Chainlink's "authorization/allowlist bypass" bounty impact class. Concretely, any authenticated or session-bound data returned by the gateway HTTP handler to a legitimate user's browser could be read by a page hosted on the attacker's suffix-colliding domain if the victim is lured to visit it while interacting with the gateway (e.g., via CSRF-like flow combined with credentials/cookies, or if the JWT/auth token is stored such that the browser attaches it).

### Likelihood Explanation
Preconditions: `CORSEnabled=true` and at least one wildcard entry configured in `CORSAllowedOrigins` (e.g., `*.allowed.com`) — a legitimate, supported configuration pattern, not a misconfiguration. The attacker only needs to register a domain name that ends with the allowed suffix (no special privileges, no other credentials) and host a page there; this is fully feasible and repeatable, requiring no operator or admin access — fitting the "unauthenticated web attacker" threat model.

### Recommendation
Enforce a boundary check for the wildcard match: after stripping `*.`, verify the origin equals the allowed host or ends with `"." + allowedHost`, e.g.:
```go
allowedHost = allowedHost[2:]
if originHost == allowedHost || strings.HasSuffix(originHost, "."+allowedHost) {
    return true
}
```

### Proof of Concept
Add a table-driven unit test for `isAllowedOrigin` in `core/services/gateway/network/httpserver_test.go`:
```go
func TestIsAllowedOrigin_WildcardBoundary(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.xallowed.com"}},
        lggr:   logger.Test(t),
    }
    // Attacker domain "notxallowed.com" ends with "xallowed.com" but is NOT a subdomain.
    assert.False(t, s.isAllowedOrigin("https://notxallowed.com"))
    // Legitimate subdomain should still pass.
    assert.True(t, s.isAllowedOrigin("https://sub.xallowed.com"))
}
```
Expected (current, buggy) result: the first assertion fails because `isAllowedOrigin` returns `true` for `notxallowed.com`, demonstrating the boundary-less suffix match bug.

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
