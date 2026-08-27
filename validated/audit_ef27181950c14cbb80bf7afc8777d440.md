### Title
CORS wildcard origin bypass via unbounded suffix match in `isAllowedOrigin` allows attacker-registered domains to spoof allowed wildcard hosts - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` implements wildcard origin matching (e.g. `*.ethereum.org`) using a raw `strings.HasSuffix` check on the hostname without validating a `.` label boundary. This lets an attacker who owns any domain that merely ends with the configured suffix string (not an actual subdomain) satisfy the check and get their `Origin` reflected into `Access-Control-Allow-Origin`.

### Finding Description
In `core/services/gateway/network/httpserver.go`, `isAllowedOrigin` handles wildcard entries as follows: [1](#0-0) 
```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if strings.HasSuffix(originHost, allowedHost) {
        return true
    }
}
```
For a configured wildcard `*.ethereum.org`, `allowedHost` becomes `ethereum.org`, and the check is `strings.HasSuffix(originHost, "ethereum.org")`. This has no requirement that the character preceding the match be a `.` (i.e., no label-boundary enforcement). As a result, any hostname that ends in the literal string `ethereum.org` — including entirely unrelated, attacker-registrable domains such as `notethereum.org`, `fake-ethereum.org`, or `evilethereum.org` — passes the check, even though these are not subdomains of `ethereum.org` at all.

This function is invoked directly from `handleRequest` for every incoming request when CORS is enabled: [2](#0-1) 
```go
if s.config.CORSEnabled {
    origin := r.Header.Get("Origin")
    if s.isAllowedOrigin(origin) {
        w.Header().Set("Access-Control-Allow-Origin", origin)
        ...
```
Since the browser sets the `Origin` header automatically based on the requesting page's true origin (not attacker-scriptable), the attacker's exploitation path is to host a malicious page on a domain they control that satisfies the flawed suffix check (e.g. `https://fake-ethereum.org`), then issue a `fetch()`/XHR with credentials to the gateway endpoint. Because the origin passes `isAllowedOrigin`, the server reflects it back verbatim in `Access-Control-Allow-Origin`, and the browser will expose the cross-origin response to the attacker's script (subject to `Access-Control-Allow-Credentials` not being separately required here, and depending on what credential/session mechanism the gateway relies on for the specific request, e.g. bearer JWT extracted from `Authorization` header in `handleRequest`).

There is no boundary check elsewhere in the code path to compensate; the only defenses are exact scheme/port equality checks, which do not mitigate the suffix issue.

### Impact Explanation
This is a genuine CORS allowlist bypass (relaxed-origin / cross-origin data exposure), not a mere misconfiguration: even with a correctly-intentioned wildcard entry like `*.ethereum.org`, the implementation itself is flawed and grants access to domains that were never meant to be trusted. This matches the "allowlist/quota bypass" and cross-origin credential-relying attack class in scope. Concretely, it enables a malicious website (on a look-alike but unrelated domain) to make authenticated cross-origin requests to the gateway HTTP API and read the JSON response in the victim's browser, which can leak gateway responses which may be scoped to the DON/gateway API rather than confidential in themselves — the severity depends on what data/actions the gateway HTTP handler (`HTTPRequestHandler.ProcessRequest`) exposes, but the vulnerability itself is a violation of the CORS request-isolation invariant.

### Likelihood Explanation
- Preconditions: operator must configure `CORSEnabled=true` and at least one wildcard entry in `CORSAllowedOrigins` (a supported, documented usage pattern, not itself a misconfiguration).
- No credentials or special role are needed by the attacker; they only need to register/host a domain string that satisfies the flawed suffix match and lure a victim's browser to visit it.
- Fully reproducible: deterministic string-comparison logic, no timing/race conditions involved.
- Feasibility is high because attacker-controlled domain registration (e.g., `fake-ethereum.org`) is trivial and cheap.

### Recommendation
Fix `isAllowedOrigin`'s wildcard branch to require a proper label boundary, e.g.:
```go
if strings.HasPrefix(allowed, "*.") {
    suffix := allowedHost[2:] // after stripping "*."
    if originHost == suffix || strings.HasSuffix(originHost, "."+suffix) {
        return true
    }
}
```
This ensures `originHost` is either exactly the base domain or a genuine subdomain (preceded by a `.`), preventing string-suffix confusion with unrelated domains.

### Proof of Concept
Add/extend a Go table test for `isAllowedOrigin` in `core/services/gateway/network/httpserver_test.go`:
```go
func TestIsAllowedOrigin_WildcardSuffixConfusion(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.ethereum.org"}},
        lggr:   logger.Test(t),
    }
    cases := []struct {
        origin string
        want   bool
    }{
        {"https://sub.ethereum.org", true},        // legitimate subdomain
        {"https://ethereum.org", false},            // no *. bare-domain rule intended? adjust per spec
        {"https://fake-ethereum.org", false},        // MUST be false; current code returns true (bug)
        {"https://notethereum.org", false},          // MUST be false; current code returns true (bug)
        {"https://evilethereum.org", false},         // MUST be false; current code returns true (bug)
    }
    for _, c := range cases {
        got := s.isAllowedOrigin(c.origin)
        assert.Equal(t, c.want, got, "origin=%s", c.origin)
    }
}
```
Running this against the current implementation demonstrates `fake-ethereum.org`, `notethereum.org`, and `evilethereum.org` incorrectly return `true`, confirming the bypass. A handler-level integration test can further confirm that `handleRequest` reflects such an `Origin` into `Access-Control-Allow-Origin` for these malicious values.

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
