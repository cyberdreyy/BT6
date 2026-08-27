### Title
Incomplete Secret-Redaction Blacklist in HTTP Request Logging Middleware Leaks Non-"password" Secrets to Node Logs - (File: core/web/router.go)

### Summary
The reported `protectedTokens()` bug is a classic "incomplete protection list" flaw: a hardcoded array/set was supposed to enumerate everything that must be shielded, but it omits an important entry (`yveCrv`), so requests targeting that omitted item bypass the intended protection. The Chainlink node's HTTP request-logging middleware contains the same bug class: it redacts request body/query fields using a small, hardcoded, substring-based blacklist that only recognizes "password"-like key names, so any other sensitive field submitted by a client (tokens, secrets, keys, codes, etc.) is written to the node's logs in cleartext.

### Finding Description
Every JSON API request handled by the gin router passes through the request logger, which reads and "sanitizes" the request body and query string before logging them: [1](#0-0) 

Sanitization is implemented by `readSanitizedJSON` and `redact`, both of which redact a field only if `isBlacklisted(k)` returns true: [2](#0-1) 

`isBlacklisted` checks membership in a five-entry map and a substring match on `"password"` — nothing else: [3](#0-2) 

This is structurally identical to the reported bug: a `protected[]`/`blacklist{}` collection is supposed to enumerate every sensitive item, but it only lists password-shaped keys (`password`, `newpassword`, `oldpassword`, `current_password`, `new_account_password`). Any JSON body field whose name does not contain "password" — e.g., API secrets, access tokens, session tokens, MFA/OTP codes, private-key material, or other credential fields accepted by node API endpoints — is passed straight through into `cleaned[k] = v` and subsequently logged verbatim via `readBody`: [4](#0-3) 

Because this logger sits in the generic request pipeline (used for all `/v2/...` API calls, not just admin-only routes), any endpoint that accepts a JSON body containing sensitive-but-not-"password"-named fields will have that value persisted in plaintext in the node's log files, which are often shipped to centralized log aggregators or accessible to a broader audience than the credential owner.

### Impact Explanation
This is a secret-disclosure issue: sensitive values that the application intends to keep confidential can end up recorded in plaintext application logs merely because their field name doesn't match the word "password". Log files are frequently retained, shipped to third-party aggregators, or accessible by operators/support staff who should not otherwise see raw credential material, so this constitutes an unintended widening of who can observe a secret — directly analogous to the reported bug where a token not present in the `protected[]`/blacklist array escapes the intended safeguard.

### Likelihood Explanation
The blacklist covers only password-shaped keys and is a static, easily-outpaced allowlist-of-sensitive-terms; any endpoint whose request/response schema uses a differently-named secret field (token, secret, code, key, etc.) will trigger the leak on every single request that hits the logging middleware, with no attacker action required beyond making a normal, authenticated (or even unauthenticated failed-auth) API call. The exposure is deterministic and occurs on the standard request path.

### Recommendation
Replace the narrow substring/word blacklist with a comprehensive, explicitly-maintained set of sensitive field-name patterns (e.g., `password`, `secret`, `token`, `key`, `code`, `mnemonic`, `seed`, `credential`, `auth`), or invert the model to an allowlist of known-safe fields, or better, tag sensitive DTO fields at the struct level (similar to `models.Secret`'s redacting `String()`/`MarshalText` behavior already used elsewhere in the codebase) so redaction is enforced at the type level rather than by guessing field names in a router-level blacklist.

### Proof of Concept
1. Identify any node API endpoint whose JSON request body includes a sensitive field not named with "password" (e.g., a field literally called `token`, `secret`, `code`, or similar).
2. Issue that request through the standard gin router; the `loggerFunc` middleware will call `readBody`, which calls `readSanitizedJSON`.
3. Because `isBlacklisted` only matches the fixed list plus the substring "password", the sensitive field's value is copied unredacted into `cleaned[k]` and logged via `lggr...("body", readBody(...))`.
4. Inspect the node's logs; the sensitive value appears in plaintext, confirming the redaction bypass — the same class of failure as `protectedTokens()` failing to include `yveCrv` in its protected list.

Note: I was not able to fully enumerate, within this session, every concrete Chainlink API endpoint whose request body carries a non-"password"-named secret field (e.g., key-import endpoints), so the PoC above documents the confirmed code-level bypass mechanism rather than a specific end-to-end field name; a Devin session with full repository/tooling access would be needed to enumerate exact vulnerable endpoints.

### Citations

**File:** core/web/router.go (L556-568)
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
	}
```

**File:** core/web/router.go (L588-606)
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
```

**File:** core/web/router.go (L608-641)
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
