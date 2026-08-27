### Title
CORS wildcard allowlist bypass via unanchored suffix match in `isAllowedOrigin` - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` implements wildcard host matching (e.g. `*.example.com`) using `strings.HasSuffix(originHost, allowedHost)` without requiring a `.` boundary before the matched suffix. This allows an attacker who registers a domain that merely *ends* with the allowed suffix string (not an actual subdomain) to be treated as an allowed origin, causing the gateway to reflect `Access-Control-Allow-Origin` for that attacker-controlled origin.

### Finding Description
In `isAllowedOrigin` (`core/services/gateway/network/httpserver.go:142-178`), when an allowlist entry has a wildcard prefix `*.`, the code strips the `*.` and does a pure string-suffix check:
```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if strings.HasSuffix(originHost, allowedHost) {
        return true
    }
}
``` [1](#0-0) 

`strings.HasSuffix` performs a raw character-suffix comparison with no requirement that the character immediately preceding the matched suffix be a `.` (i.e., an actual DNS label boundary). Consequently, for an allowlist entry `*.example.com` (allowedHost = `example.com`, 11 chars), any origin host whose trailing 11 characters spell exactly `example.com` will match — even if that host is a completely different, attacker-registered domain such as `eviledexample.com`. This is because `HasSuffix` treats the string purely as a byte sequence: `eviledexample.com` ends in the literal characters `e-x-a-m-p-l-e-.-c-o-m`, satisfying the check despite not being a subdomain of `example.com` at all.

This is reachable directly from `handleRequest` (`core/services/gateway/network/httpserver.go:180-194`), which is the top-level HTTP handler for every gateway request (including unauthenticated `OPTIONS` preflights and `GET`/`POST`): the `Origin` header is attacker-controlled input, requires no authentication, and is passed straight into `isAllowedOrigin`. If it returns `true`, the server reflects the raw `Origin` value into `Access-Control-Allow-Origin`. [2](#0-1) 

No existing validation compensates for this: scheme and port must match exactly, and exact-host equality is checked first, but the wildcard branch has no dot-boundary enforcement.

### Impact Explanation
This is an allowlist/CORS-isolation bypass. With `CORSEnabled=true` and any wildcard entry like `*.example.com` configured, an attacker who registers or controls a domain ending in the exact allowed-suffix bytes (e.g. `eviledexample.com`) can serve a malicious web page from that origin. Browsers visiting that page will see the gateway respond with `Access-Control-Allow-Origin: https://eviledexample.com`, permitting the page's JavaScript to make and read cross-origin requests/responses from the gateway API that were intended to be restricted to genuine `*.example.com` subdomains — a cross-user CORS bypass / response confusion (matches the "allowlist/quota bypass" and "cross-user response confusion" impact classes).

### Likelihood Explanation
The only precondition is a gateway operator enabling CORS with a wildcard entry (a documented, supported configuration — see `sample_config.toml`), which is common when subdomain-based frontends need access. No credentials, roles, or special access are required by the attacker beyond registering a domain string and getting a victim to load a page from it; the check is fully deterministic and repeatable.

### Recommendation
Anchor the wildcard suffix match to a DNS label boundary, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    suffix := allowedHost[1:] // ".example.com"
    if originHost == allowedHost[2:] || strings.HasSuffix(originHost, suffix) {
        return true
    }
}
```
i.e. compare against `"."+allowedHost` (with the leading dot preserved) rather than the bare `allowedHost`, ensuring only genuine subdomains match.

### Proof of Concept
Add a table-driven test to `core/services/gateway/network/httpserver_test.go` exercising `isAllowedOrigin` with:
- `CORSAllowedOrigins = ["*.example.com"]`
- Origin `https://eviledexample.com` → currently returns `true`; assert it should return `false` after fix.
- Origin `https://legit.example.com` → should remain `true` (regression guard).
- Origin `https://notreallyexample.com` (another suffix-colliding variant) → should return `false`. [3](#0-2)

### Citations

**File:** core/services/gateway/network/httpserver.go (L142-178)
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
