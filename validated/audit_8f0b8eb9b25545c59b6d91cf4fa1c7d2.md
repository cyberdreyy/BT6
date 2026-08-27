### Title
Incomplete sensitive-field blacklist in HTTP request logging leaks secrets to logs - (File: core/web/router.go)

### Summary
The Guard.sol report describes a vulnerability class where a security check relies on an incomplete denylist of "dangerous" operations (`burn`, `burnFrom`, `permit`) — the guard only checks for a fixed set of transfer-related selectors and silently lets anything else through, leading to asset loss. The Chainlink node has a structurally identical pattern in its HTTP request-logging middleware: request bodies and query parameters are sanitized against a hardcoded, narrow blacklist of key names before being written to logs, and any sensitive field whose name isn't in that list is logged in plaintext.

### Finding Description
`loggerFunc` is installed as global request-logging middleware and logs every request body and query string at debug level: [1](#0-0) 

The body is "sanitized" via `readBody` → `readSanitizedJSON`, which only redacts **top-level** JSON keys that match `isBlacklisted`: [2](#0-1) 

Query parameters go through the same `isBlacklisted` check in `redact`: [3](#0-2) 

The blacklist itself only contains password-related keys, matched via an exact set plus a substring check for `"password"`: [4](#0-3) 

Exactly like `Guard.sol`'s selector check — which enumerates `safeTransferFrom`/`transferFrom`/`approve`/etc. but omits `burn`, `burnFrom`, `permit` — this redaction logic enumerates only password-shaped field names and omits every other class of secret: API keys/tokens, private keys, seed phrases, bridge/API secrets, or arbitrary secrets embedded inside larger blobs (e.g., a job spec's `toml` field, which can contain hardcoded `Authorization` headers or API keys for HTTP bridge tasks). Because the check is also shallow (only top-level map keys are inspected, not nested objects/arrays), any sensitive value nested one level deep, or under a key not literally containing "password", passes through unredacted into the debug log stream.

### Impact Explanation
Any authenticated node-API client (or external-initiator webhook caller, whose requests flow through the same gin router/middleware stack) that submits a request body containing secret material under a key name other than the whitelisted password variants — e.g. a job-creation TOML embedding an HTTP task with a hardcoded API key/bearer token, or any future/plugin endpoint accepting a `token`, `secret`, `apiKey`, `privateKey`, or `mnemonic` field — will have that value written verbatim to the node's debug logs. This is a secret-disclosure issue: anyone with read access to node logs (log aggregators, support staff, misconfigured log shipping) recovers credentials that were never intended to be persisted in plaintext, directly analogous to how the incomplete Guard.sol selector list allowed assets to be silently drained via unchecked functions.

### Likelihood Explanation
Requires the node to be run with debug-level logging enabled (a common operational setting, not the default in some deployments) and a request whose sensitive field name doesn't match the "password" substring rule. Given how many node endpoints accept free-form JSON/TOML payloads (job specs, bridge configs, external-initiator run bodies), the odds of a non-"password"-named secret being logged are non-trivial, but actual exploitation depends on log level and log access, which somewhat limits likelihood compared to a fully unprivileged, always-on path.

### Recommendation
Broaden and centralize the redaction logic instead of an ad hoc keyword blacklist:
- Match on a wider set of substrings (`secret`, `token`, `apikey`, `api_key`, `privatekey`, `private_key`, `mnemonic`, `seed`, `authorization`, `credential`), not just `password`.
- Recursively walk nested JSON objects/arrays rather than only top-level keys.
- Prefer an explicit allowlist of loggable fields per endpoint, or reuse the existing `models.Secret`/`SecretString` wrapper type consistently so any field typed as a secret is automatically masked regardless of its JSON key name.
- For free-form blobs like job-spec TOML, avoid logging the raw body at all, or apply the same TOML-aware secret-redaction used elsewhere in config handling (`docs/SECRETS.md`/`Secrets.setEnv`) before logging.

### Proof of Concept
1. Enable `Log.Level = 'debug'` on a running Chainlink node.
2. Send an authenticated POST request to any JSON-accepting endpoint (e.g. `/v2/jobs`) with a body such as:
   ```json
   {"toml": "...\n[headers]\nAuthorization = \"Bearer super-secret-api-key\"\n..."}
   ```
   or any endpoint accepting a body with a key like `{"apiKey": "sk-live-XXXX"}`.
3. Observe the node's debug log output produced by `loggerFunc`/`readSanitizedJSON`: [1](#0-0)  — the `apiKey`/embedded-token value appears unredacted because `isBlacklisted` only matches password-like keys: [5](#0-4)

### Citations

**File:** core/web/router.go (L556-567)
```go
		lggr.Debugw(fmt.Sprintf("%s %s", c.Request.Method, c.Request.URL.Path),
			"method", c.Request.Method,
			"status", c.Writer.Status(),
			"path", c.Request.URL.Path,
			"ginPath", c.FullPath(),
			"query", redact(c.Request.URL.Query()),
			"body", readBody(rdr, lggr),
			"clientIP", c.ClientIP(),
			"errors", c.Errors.String(),
			"servedAt", end.Format("2006-01-02 15:04:05"),
			"latency", fmt.Sprintf("%v", end.Sub(start)),
		)
```

**File:** core/web/router.go (L608-629)
```go
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

**File:** core/web/router.go (L631-641)
```go
func redact(values url.Values) string {
	cleaned := url.Values{}
	for k, v := range values {
		if isBlacklisted(k) {
			cleaned[k] = []string{"REDACTED"}
			continue
		}
		cleaned[k] = v
	}
	return cleaned.Encode()
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
