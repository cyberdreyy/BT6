### Title
CORS `isAllowedOrigin` wildcard suffix match lacks label boundary, allowing attacker-controlled domains to bypass origin allowlist - ([File: core/services/gateway/network/httpserver.go])

### Summary
`isAllowedOrigin` implements wildcard matching (`*.example.com`) using `strings.HasSuffix(originHost, allowedHost)` after stripping the `*.` prefix, without requiring a `.` boundary before the suffix. This lets any attacker-registered domain that merely *ends with* the configured suffix string (e.g. `evilremix.com` against a `*.remix.com` allowlist entry) be treated as an allowed subdomain, causing the gateway to reflect `Access-Control-Allow-Origin` for that attacker domain.

### Finding Description
In `httpServer.isAllowedOrigin` [1](#0-0) , wildcard entries are matched with:
```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if strings.HasSuffix(originHost, allowedHost) {
        return true
    }
}
```
There is no check that the character preceding the matched suffix in `originHost` is a `.` (label boundary). Consequently, for an allowlist entry `*.remix.com` (intended to permit only proper subdomains like `foo.remix.com`), an attacker who registers `evilremix.com` produces an `originHost` of `evilremix.com`, which satisfies `strings.HasSuffix("evilremix.com", "remix.com") == true`, even though `evilremix.com` is not a subdomain of `remix.com` at all — it is an entirely different, attacker-controlled domain.

This is invoked from `handleRequest` [2](#0-1) , which reflects the raw `Origin` header value into `Access-Control-Allow-Origin` whenever `isAllowedOrigin` returns true, for every request path served through the gateway HTTP endpoint. Scheme and port are checked with strict equality, and exact-host match is strict; the flaw is isolated to the wildcard-suffix branch.

The existing unit tests [3](#0-2)  only cover suffix mismatches that don't share the tail characters, not the case where an unrelated domain ending in the same character sequence as the allowed suffix is presented — so this gap is untested.

### Impact Explanation
An operator who configures a wildcard CORS allowlist entry (e.g. `*.chain.link`, `*.remix.com`) to permit legitimate subdomains inadvertently also allows any attacker-registered domain whose name ends with that same string (`evilchain.link`, `evilremix.com`, `notchain.link`, etc.) — domains fully controllable by any external party via normal domain registration, not requiring DNS control over the legitimate parent domain. This corresponds to a cross-origin response confusion bug: a malicious page hosted on such a look-alike domain would receive `Access-Control-Allow-Origin` reflecting itself, allowing browser JS on that origin to read gateway responses via `fetch`/`XHR` for any requests it can construct. The severity is bounded by what a browser CORS bypass with header-based (non-cookie) bearer auth can actually achieve: the response notably does **not** set `Access-Control-Allow-Credentials`, so browsers will not allow this to be combined with credentialed (cookie) requests, and the attacker page cannot read a bearer JWT it does not already possess to forge the `Authorization` header. So the exploitable surface is limited to gateway endpoints that don't require secret/JWT knowledge, or session data attached automatically by the browser without credentials mode — this narrows real cross-user impact versus the fully weaponized scenario described in the question (`victimdomain.com.attacker.com` style reflected auth theft), but the underlying allowlist-bypass defect is real and independently exploitable as an origin-validation logic bug, not merely "misconfiguration," since it silently breaks the security guarantee that a wildcard entry only matches genuine subdomains.

### Likelihood Explanation
Exploitability requires: (1) the gateway operator has configured at least one wildcard CORS entry, which is a documented/supported feature (see wildcard test cases and `sample_config.toml`), and (2) the attacker registers or hosts a domain whose name happens to end with the same string as the configured suffix (trivial and unprivileged — normal domain registration, no gateway credentials needed). No authentication, no network position, and no operator/admin access is required by the attacker. Given the ease of registering suffix-colliding domains, likelihood for gateways using wildcard CORS is high; for gateways using only exact-origin lists it is not applicable.

### Recommendation
Enforce a proper label boundary when matching wildcard suffixes, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    suffix := allowedHost[1:] // ".remix.com" (keep the dot)
    if strings.HasSuffix(originHost, suffix) {
        return true
    }
}
```
i.e., compare against `.remix.com` (with the leading dot retained) instead of stripping both `*` and `.`, so `evilremix.com` no longer matches while `foo.remix.com` still does. Additionally, consider setting `Access-Control-Allow-Credentials` explicitly to `false`/omitting it (already the case) and documenting that wildcard entries must be scoped carefully, plus adding an explicit `Vary: Origin` header for cache correctness.

### Proof of Concept
Add to `core/services/gateway/network/httpserver_test.go`:
```go
func TestHTTPServer_HandleRequest_CORSEnabled_WildcardSuffixBypass(t *testing.T) {
    t.Parallel()
    _, handler, url := startNewServer(t, 100_000, 100_000, true,
        []string{"https://*.remix.com"})

    handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

    // "evilremix.com" is NOT a subdomain of remix.com, but shares the tail string "remix.com"
    origin := "https://evilremix.com"
    resp, _ := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)

    // Expected (secure) behavior: no CORS headers reflected
    require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"),
        "wildcard suffix match incorrectly treated attacker domain as an allowed subdomain")
}
```
Running this against the current `isAllowedOrigin` implementation demonstrates `Access-Control-Allow-Origin: https://evilremix.com` is set, confirming the bypass; after applying the fix (matching on `.remix.com` with the dot retained), the assertion passes.

### Citations

**File:** core/services/gateway/network/httpserver.go (L142-177)
```go
func (s *httpServer) isAllowedOrigin(origin string) bool {
	originScheme, originHost, originPort, err := s.splitURL(origin)
	if err != nil {
		s.lggr.Debug("error parsing origin URL", err)
		return false
	}
	for _, allowed := range s.config.CORSAllowedOrigins {
		// probably better to do this once when server starts and store it in a map
		// this is an easier solution so we don't have to apply more changes to the code
		// just need to be careful when specifying allowed origins in the config file
		allowedScheme, allowedHost, allowedPort, err := s.splitURL(allowed)
		if err != nil {
			s.lggr.Debug("error parsing allowed origin URL", err)
			continue
		}
		// skip if the scheme doesn't match at all
		if originScheme != allowedScheme {
			continue
		}
		// skip if the port doesn't match at all
		if originPort != allowedPort {
			continue
		}
		// check for exact host match (e.g., remix.com)
		if originHost == allowedHost {
			return true
		}
		// check for wildcard host match (e.g., *.remix.com)
		if strings.HasPrefix(allowedHost, "*.") {
			allowedHost = allowedHost[2:]
			if strings.HasSuffix(originHost, allowedHost) {
				return true
			}
		}
	}
	return false
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

**File:** core/services/gateway/network/httpserver_test.go (L181-194)
```go
func TestHTTPServer_HandleRequest_CORSEnabled_FromNotAllowedOriginWildcards(t *testing.T) {
	t.Parallel()
	_, handler, url := startNewServer(t, 100_000, 100_000, true,
		[]string{"https://*.ethereum.org", "https://*.valid.domain.com", "http://example.gov:8080"})

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin := "https://ethereum.remix.org" // doesn't end with ethereum.org
	resp, respBytes := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Methods"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Headers"))
```
