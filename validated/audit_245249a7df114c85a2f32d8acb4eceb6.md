### Title
CORS wildcard origin check missing dot-boundary allows subdomain-spoofing bypass (`isAllowedOrigin`) - ([File: core/services/gateway/network/httpserver.go])

### Summary
`httpServer.isAllowedOrigin` matches wildcard entries like `*.remix.com` using `strings.HasSuffix(originHost, allowedHost)` without requiring a `.` (dot) boundary before the suffix. This lets an attacker-controlled origin such as `https://evilremix.com` be treated as a subdomain of `remix.com` and pass the allowlist check.

### Finding Description
In `isAllowedOrigin` [1](#0-0) , when an allowed origin is configured as a wildcard (e.g. `https://*.remix.com`), the code strips the `*.` prefix leaving `remix.com`, then checks `strings.HasSuffix(originHost, allowedHost)`. This check only verifies that `originHost` ends with the literal string `remix.com` — it does not verify that the character immediately preceding that suffix is a `.` (or that the suffix begins at a label boundary). Consequently, any host that merely ends with the same characters — including `evilremix.com`, `notremix.com`, or `xremix.com` — will incorrectly satisfy the suffix check and be treated as an allowed subdomain of `remix.com`.

This function is invoked directly from `handleRequest` [2](#0-1) , which reflects the attacker-supplied `Origin` header value verbatim into the `Access-Control-Allow-Origin` response header whenever `isAllowedOrigin` returns `true`. Since the `Origin` header is fully attacker-controlled (any unauthenticated caller of the gateway's public HTTP endpoint), and there is no additional validation elsewhere that constrains this suffix matching, an attacker hosting `https://evilremix.com` can have their browser-based origin treated as trusted, enabling cross-origin reads of gateway responses that were intended to be restricted to `*.remix.com` subdomains.

### Impact Explanation
This is a CORS allowlist bypass: an operator who intends to trust only genuine subdomains of `remix.com` (e.g., `app.remix.com`, `admin.remix.com`) will unintentionally also trust attacker-registered domains like `evilremix.com`. Because `Access-Control-Allow-Origin` is reflected for the bypassing origin, a malicious website at that domain can make credentialed/cross-origin browser requests to the gateway and read responses that should be confined to authorized origins — enabling cross-user response confusion / unauthorized read access to gateway API responses from an origin the operator did not intend to trust. This maps to a CORS/allowlist-bypass authorization issue.

### Likelihood Explanation
Preconditions: `CORSEnabled=true` and at least one wildcard entry in `CORSAllowedOrigins` (e.g., `https://*.remix.com`) — a realistic and supported configuration pattern per the existing wildcard-handling code. No credentials, roles, or special access are required; any unprivileged party can register/host a domain like `evilremix.com` and simply set the `Origin` header on requests to the gateway's HTTP endpoint. This is fully attacker-controlled, deterministic, and repeatable.

### Recommendation
Require a label boundary when matching the wildcard suffix, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    suffix := allowedHost[1:] // ".remix.com" (keep the dot)
    if originHost == allowedHost[2:] || strings.HasSuffix(originHost, suffix) {
        return true
    }
}
```
This ensures `originHost` must end with `.remix.com` (dot included) or equal `remix.com` exactly, preventing `evilremix.com` from matching.

### Proof of Concept
Add to `core/services/gateway/network/httpserver_test.go`:
```go
func Test_isAllowedOrigin_WildcardSuffixBypass(t *testing.T) {
    s := &httpServer{
        config: &HTTPServerConfig{
            CORSAllowedOrigins: []string{"https://*.remix.com"},
        },
        lggr: logger.Test(t),
    }

    // Should be allowed: legitimate subdomain
    require.True(t, s.isAllowedOrigin("https://app.remix.com"))

    // Should be rejected: attacker-controlled domain that merely shares the suffix
    require.False(t, s.isAllowedOrigin("https://evilremix.com"))
}
```
Expected (current behavior, demonstrating the bug): `isAllowedOrigin("https://evilremix.com")` returns `true` instead of `false`, confirming the missing dot-boundary check causes an unauthorized origin to be reflected via `Access-Control-Allow-Origin`.

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
