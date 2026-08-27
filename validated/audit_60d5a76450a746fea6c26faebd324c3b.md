### Title
CORS wildcard suffix-match bypass in `isAllowedOrigin` allows attacker-registered domains to be treated as trusted origins - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` implements wildcard origin matching (`*.example.com`) using a raw `strings.HasSuffix` check on the hostname without verifying a `.` boundary before the suffix. This lets an attacker register any domain that merely ends with the allowed suffix (e.g. `evilexample.com` matching `*.example.com`) and have the gateway reflect `Access-Control-Allow-Origin` for that attacker-controlled origin, weakening the intended CORS allowlist.

### Finding Description
In `isAllowedOrigin`, wildcard entries are handled as: [1](#0-0) 
The code strips the `*.` prefix and then calls `strings.HasSuffix(originHost, allowedHost)`. This check does not require that the character preceding the matched suffix in `originHost` be a `.`, so a hostname like `evilexample.com` satisfies `HasSuffix("evilexample.com", "example.com")` even though it is not a subdomain of `example.com` at all — it's an entirely different, attacker-registrable domain.

`handleRequest` uses the result directly to set the CORS header: [2](#0-1) 
Since `origin` (attacker-controlled, taken verbatim from the request's `Origin` header) is reflected back in `Access-Control-Allow-Origin` whenever `isAllowedOrigin` returns true, an attacker who registers a domain ending in the same suffix as a configured `*.<domain>` entry (e.g. `notexample.com`, `fakeexample.com`, or even `evilAAAexample.com`) will have their origin accepted, even though it is not a legitimate subdomain of the allowed domain.

This is reachable by any unauthenticated client — no credential is required to trigger or test the header logic, since the CORS branch runs before authentication/handler dispatch (`ProcessRequest` is only called afterward and JWT is only checked deeper by the handler, not by this CORS gate).

### Impact Explanation
This weakens the intended origin allowlist boundary at the transport layer. If a browser-based caller of the gateway ever holds credentials that browsers attach automatically or that a malicious page can trigger to be sent (e.g. a stored bearer token exposed to script, or future use of cookies), an attacker-registered domain matching the loose suffix check would be treated as trusted and could read cross-origin gateway responses that should be restricted to `*.example.com` subdomains. This matches an origin-isolation / allowlist-bypass class of impact — the allowlist's isolation guarantee ("only subdomains of the configured domain are permitted") is broken by domains that merely share a suffix, not a subdomain relationship.

### Likelihood Explanation
Exploitability of the matching logic itself requires no privileges: any party can send a crafted `Origin` header and observe `Access-Control-Allow-Origin` reflect it. The remaining risk (actual credential exfiltration) depends on operator configuration (use of wildcard `*.` entries in `CORSAllowedOrigins`) and on a browser client legitimately holding gateway credentials, so real-world impact is conditioned on deployment specifics, but the code-level allowlist flaw itself is deterministic and trivially reproducible.

### Recommendation
Fix the wildcard suffix check to require a domain boundary, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    suffix := allowedHost[1:] // keep the leading dot: ".example.com"
    if strings.HasSuffix(originHost, suffix) {
        return true
    }
}
```
This ensures `originHost` must end in `.example.com` (a real subdomain separator), rejecting `evilexample.com` while still matching `foo.example.com`.

### Proof of Concept
Add a table-driven Go test for `isAllowedOrigin` in `core/services/gateway/network/httpserver_test.go`:
```go
func TestIsAllowedOrigin_WildcardBoundary(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.example.com"}},
        lggr:   logger.Test(t),
    }
    cases := []struct{
        origin string
        want   bool
    }{
        {"https://foo.example.com", true},   // legit subdomain
        {"https://example.com", false},      // bare domain, not covered by *.
        {"https://evilexample.com", false},  // should be rejected, currently matches due to bare HasSuffix
        {"https://notexample.com", false},   // same class of bypass
    }
    for _, c := range cases {
        got := s.isAllowedOrigin(c.origin)
        assert.Equal(t, c.want, got, "origin=%s", c.origin)
    }
}
```
Running this against the current implementation shows `https://evilexample.com` and `https://notexample.com` incorrectly return `true`, confirming the suffix-boundary bypass.

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
