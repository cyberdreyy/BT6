### Title
CORS wildcard origin bypass via missing dot-boundary check in suffix match - ([File: core/services/gateway/network/httpserver.go])

### Summary
The `httpServer.isAllowedOrigin` function validates wildcard entries (e.g. `*.ethereum.org`) using `strings.HasSuffix(originHost, allowedHost)` after stripping the `*.` prefix, but it never verifies that the matched suffix is preceded by a `.` boundary. An attacker who registers a domain that merely ends with the same characters as the allowed suffix (e.g. `evilethereum.org` for an allowlisted `*.ethereum.org`) can pass the origin check and receive a valid `Access-Control-Allow-Origin` response, defeating the intended subdomain restriction.

### Finding Description
In `isAllowedOrigin` [1](#0-0) , wildcard entries strip the `*.` prefix and then call `strings.HasSuffix(originHost, allowedHost)`. This check only verifies that `originHost` ends with the literal characters of `allowedHost`; it does not require a `.` immediately before the match, so any registrable domain that happens to end with the same character sequence (not just a true subdomain) satisfies the condition. For example, if the gateway operator configures `CORSAllowedOrigins: ["https://*.ethereum.org"]`, an attacker who owns the domain `https://evilethereum.org` can send that value in the `Origin` header. `splitURL` normalizes it to host `evilethereum.org`, and `strings.HasSuffix("evilethereum.org", "ethereum.org")` returns `true`, so `isAllowedOrigin` returns `true`. `handleRequest` then reflects the attacker origin back via `Access-Control-Allow-Origin` [2](#0-1) , which browsers use to permit the attacker's page to read the gateway's cross-origin response. No other check (scheme/port match at lines 158-164) blocks this since scheme and port checks pass independently of host validation.

### Impact Explanation
This allows an attacker-controlled web page hosted on a look-alike domain to bypass the gateway's CORS allowlist and make credentialed/authenticated cross-origin requests to the Gateway HTTP API, reading responses that were intended to be restricted to the operator's allowed origins (e.g. `remix.ethereum.org`-style trusted front-ends). This corresponds to an allowlist/CORS bypass leading to cross-user/cross-origin response confusion and potential credential or data disclosure to an attacker-controlled origin.

### Likelihood Explanation
Exploitation requires no privileges beyond being able to register an arbitrary domain name (trivial, low-cost) and lure a victim browser to visit an attacker page — a standard unauthenticated browser-based attack. It is deterministic and repeatable against any gateway deployment that uses wildcard entries in `CORSAllowedOrigins`, which the codebase's own tests demonstrate as a supported/expected configuration pattern [3](#0-2) .

### Recommendation
Fix the wildcard match to require a dot boundary, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    suffix := allowedHost[1:] // keep leading dot, e.g. ".ethereum.org"
    if strings.HasSuffix(originHost, suffix) {
        return true
    }
}
```
This ensures `originHost` must end with `.ethereum.org` (a true subdomain boundary), rejecting look-alike domains such as `evilethereum.org`.

### Proof of Concept
Add a table test case to `core/services/gateway/network/httpserver_test.go` analogous to `TestHTTPServer_HandleRequest_CORSEnabled_FromNotAllowedOriginWildcards`:
1. Start server with `CORSAllowedOrigins: []string{"https://*.ethereum.org"}`.
2. Send request with `Origin: https://evilethereum.org`.
3. Assert current (vulnerable) behavior: `resp.Header.Get("Access-Control-Allow-Origin") == "https://evilethereum.org"` (should be empty after fix).
4. After applying the recommended fix, re-run and assert `Access-Control-Allow-Origin` header is empty for this origin, while `https://sub.ethereum.org` still correctly returns the header.

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

**File:** core/services/gateway/network/httpserver_test.go (L115-128)
```go
func TestHTTPServer_HandleRequest_CORSEnabled_FromAllowedOriginWildcards(t *testing.T) {
	t.Parallel()
	_, handler, url := startNewServer(t, 100_000, 100_000, true,
		[]string{"https://*.ethereum.org", "https://*.valid.domain.com", "http://*.gov"})

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin := "https://remix.ethereum.org"
	resp, respBytes := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Equal(t, origin, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Equal(t, "GET, POST, OPTIONS", resp.Header.Get("Access-Control-Allow-Methods"))
	require.Equal(t, "Content-Type", resp.Header.Get("Access-Control-Allow-Headers"))
```
