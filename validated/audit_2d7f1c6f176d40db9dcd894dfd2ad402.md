### Title
Incomplete sensitive-field denylist in HTTP request-body logging leaks non-password secrets to node logs - ([File: core/web/router.go])

### Summary
The Notional bug (M-8) is a "curated denylist that omits a critical entry" bug class: `_isInvalidRewardToken` enumerates tokens that must never be swapped away, but forgets to list the BPT LP token, so a value that should always be blocked slips through the filter. The same *incomplete-denylist* pattern exists in the chainlink node's HTTP request logging middleware: `isBlacklisted` is meant to keep sensitive request-body fields out of the node's logs, but its hardcoded list only recognizes password-shaped keys and misses other equally sensitive keys (e.g. `secret`, `token`, `apiKey`, `accessKey`, `privateKey`) that legitimately appear in request payloads.

### Finding Description
`loggerFunc` is installed as global middleware on the node's gin engine via `engine.Use(...)` in `NewRouter`, so it runs for every request made to the node's HTTP API, including unauthenticated/unprivileged endpoints, before the authentication middleware has even rejected the request. [1](#0-0) 

For every request it reads and re-buffers the body, then calls `readBody`, which parses the JSON body into a map and redacts only keys that match `isBlacklisted`: [2](#0-1) 

The denylist itself is hardcoded to password-only variants: [3](#0-2) 

```go
var blacklist = map[string]struct{}{
	"password":             {},
	"newpassword":          {},
	"oldpassword":          {},
	"current_password":     {},
	"new_account_password": {},
}

func isBlacklisted(k string) bool {
	lk := strings.ToLower(k)
	if _, ok := blacklist[lk]; ok || strings.Contains(lk, "password") {
		return true
	}
	return false
}
```

This mirrors the audit finding exactly: the filter enumerates a specific, "safe" set of values to protect (passwords) but fails to generalize to the broader class of sensitive material (secrets/tokens/keys) that the system is known to accept in request bodies elsewhere. For example, `auth.Token` (`AccessKey`/`Secret`) is the node's core API-credential model, and external-initiator/API-token flows pass `secret`-named fields around the codebase. [4](#0-3) [5](#0-4) 

If any current or future JSON POST/PATCH endpoint accepts a field literally named `secret`, `token`, `apiKey`, `accessKey`, or `privateKey` (rather than one containing the substring "password"), that value is written to the node's Debug-level logs in cleartext via `lggr.Debugw(..., "body", readBody(rdr, lggr), ...)`, because `isBlacklisted` never matches it. The value bypasses the filter for exactly the reason the report describes: the specific dangerous case was not "explicitly defined" in the guard, just as `BALANCER_POOL_TOKEN` was not explicitly defined in `_isInvalidRewardToken`.

### Impact Explanation
Any secret-bearing field name that isn't literally a "password" variant is not redacted and lands in the node's logs. Depending on the node's `LogLevel` and log shipping configuration (log aggregation, log file retention, remote log forwarding), this can result in disclosure of API secrets/tokens to anyone with log access, which is a broader audience than those with direct database or session access. This is a defense-in-depth/secret-redaction gap rather than a direct fund-movement bug, but it is the same root cause pattern flagged as valid in the audit (an allow/deny filter missing an explicitly named sensitive value), and it sits in the unprivileged-reachable, internet-facing HTTP entrypoint of the node (the middleware runs on every request regardless of authentication outcome).

### Likelihood Explanation
Likelihood is moderate and depends on whether any request body in the current API surface (or plugins/extensions built on `chainlink.Application`) uses a sensitive key name outside the "password" family. The search performed did not conclusively find a currently-wired REST endpoint whose JSON body field is literally named `secret`/`token`/`apiKey` today, so this is reported as a latent gap in the redaction control rather than a confirmed live leak of a specific credential — the underlying flaw (the denylist's incompleteness) is nonetheless directly analogous to the referenced bug class and independently verifiable in the code.

### Recommendation
Broaden `isBlacklisted` from an exact/substring match on "password" to cover the full class of sensitive field names by substring-matching on `password`, `secret`, `token`, `apikey`, `accesskey`, and `privatekey` (case-insensitive), similar to how the recommended fix for M-8 added the missing `BALANCER_POOL_TOKEN` check rather than relying on an incomplete enumeration.

### Proof of Concept
1. Add (or identify) any JSON endpoint reachable through the router's global middleware chain that accepts a body field named e.g. `"secret": "<value>"` or `"apiKey": "<value>"` (not matching `password`).
2. Send a request to that endpoint with `LogLevel=debug`.
3. Observe the node's logs: `loggerFunc` → `readBody` → `readSanitizedJSON` will emit the field with its plaintext value because `isBlacklisted("secret")` and `isBlacklisted("apikey")` both return `false`. [6](#0-5)

### Citations

**File:** core/web/router.go (L64-72)
```go
	engine.Use(
		otelgin.Middleware("chainlink-web-routes",
			otelgin.WithTracerProvider(otel.GetTracerProvider())),
		limits.RequestSizeLimiter(config.WebServer().HTTPMaxSize()),
		loggerFunc(app.GetLogger()),
		gin.Recovery(),
		cors,
		secureMiddleware(tls.ForceRedirect(), tls.Host(), config.Insecure().DevWebServer()),
	)
```

**File:** core/web/router.go (L588-629)
```go
func readBody(reader io.Reader, lggr logger.Logger) string {
	buf := new(bytes.Buffer)
	_, err := buf.ReadFrom(reader)
	if err != nil {
		lggr.Warn("unable to read from body for sanitization: ", err)
		return "*FAILED TO READ BODY*"
	}

	if buf.Len() == 0 {
		return ""
	}

	s, err := readSanitizedJSON(buf)
	if err != nil {
		lggr.Warn("unable to sanitize json for logging: ", err)
		return "*FAILED TO READ BODY*"
	}
	return s
}

func readSanitizedJSON(buf *bytes.Buffer) (string, error) {
	var dst map[string]any
	err := json.Unmarshal(buf.Bytes(), &dst)
	if err != nil {
		return "", err
	}

	cleaned := map[string]any{}
	for k, v := range dst {
		if isBlacklisted(k) {
			cleaned[k] = "*REDACTED*"
			continue
		}
		cleaned[k] = v
	}

	b, err := json.Marshal(cleaned)
	if err != nil {
		return "", err
	}
	return string(b), err
}
```

**File:** core/web/router.go (L643-658)
```go
// NOTE: keys must be in lowercase for case insensitive match
var blacklist = map[string]struct{}{
	"password":             {},
	"newpassword":          {},
	"oldpassword":          {},
	"current_password":     {},
	"new_account_password": {},
}

func isBlacklisted(k string) bool {
	lk := strings.ToLower(k)
	if _, ok := blacklist[lk]; ok || strings.Contains(lk, "password") {
		return true
	}
	return false
}
```

**File:** core/auth/auth.go (L21-30)
```go
// Token is used for API authentication.
type Token struct {
	AccessKey string `json:"accessKey"`
	Secret    string `json:"secret"`
}

// GetID returns the ID of this structure for jsonapi serialization.
func (ta *Token) GetID() string {
	return ta.AccessKey
}
```

**File:** core/web/presenters/external_initiators.go (L12-20)
```go
// ExternalInitiatorAuthentication includes initiator and authentication details.
type ExternalInitiatorAuthentication struct {
	Name           string        `json:"name,omitempty"`
	URL            models.WebURL `json:"url"`
	AccessKey      string        `json:"incomingAccessKey,omitempty"`
	Secret         string        `json:"incomingSecret,omitempty"`
	OutgoingToken  string        `json:"outgoingToken,omitempty"`
	OutgoingSecret string        `json:"outgoingSecret,omitempty"`
}
```
