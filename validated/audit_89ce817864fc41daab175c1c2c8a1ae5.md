### Title
Password values in nested GraphQL `variables` bypass the flat-key blacklist filter and are logged in plaintext by `loggerFunc`/`readSanitizedJSON` - ([File: core/web/router.go])

### Summary
`loggerFunc` (registered globally via `engine.Use(...)`) logs every request body at debug level after passing it through `readBody`/`readSanitizedJSON`. `readSanitizedJSON` unmarshals the body into a flat `map[string]any` and only redacts top-level keys matching `isBlacklisted`. Because GraphQL requests to `POST /query` wrap all mutation arguments inside a top-level `"variables"` object, a nested `password` field (e.g. from the `createAPIToken`/session-confirming mutations) is never inspected and is re-marshaled and logged verbatim.

### Finding Description
`loggerFunc` is attached to the whole `gin.Engine` at [1](#0-0) , so it runs for every route, including the authenticated GraphQL endpoint `POST /query` registered at [2](#0-1) . On every request it buffers the body, calls `c.Next()`, then logs the sanitized body via `lggr.Debugw(..., "body", readBody(rdr, lggr), ...)` at [3](#0-2) .

`readBody` delegates to `readSanitizedJSON`, which unmarshals the raw JSON into a **flat** `map[string]any` and only redacts values whose **top-level key** matches `isBlacklisted` [4](#0-3) . `isBlacklisted` only does a case-insensitive substring check against the literal key name (`"password"`, `"newpassword"`, etc.) [5](#0-4) ; it never recurses into nested objects/arrays.

A GraphQL request body has the shape `{"query": "...", "variables": {"input": {"password": "secret123"}}}`. Since the top-level keys are `query` and `variables` (neither containing "password"), `cleaned["variables"] = v` copies the entire nested map — including the plaintext password — unchanged, and it is marshaled back into the logged string in full.

This is exploitable against any GraphQL mutation whose input embeds a password-like field nested under `variables` (e.g., account/token mutations that require password confirmation). Note that logging happens for the raw HTTP request body regardless of the outcome of downstream authorization/resolver checks, because `loggerFunc` reads the body before `c.Next()` and logs after; the GraphQL/session auth middleware (`auth.AuthenticateGQL`) only gates whether the request is *processed*, not whether the body is logged.

### Impact Explanation
When the node's log level is set to Debug (a supported, non-default but legitimate operational configuration), any authenticated caller capable of reaching `POST /query` can cause their own submitted password value to be written to node logs in cleartext. This is a secret/credential-disclosure class issue: if logs are shipped to a shared/aggregated logging system, or accessible to other principals with read access to node logs (log-shipping infra, support/ops tooling, misconfigured log retention), the password is disclosed outside the confines of the original TLS-protected request. It matches "sensitive information / secret disclosure via logs" impact class.

### Likelihood Explanation
- Requires only an authenticated node API/GraphQL caller (any role — the body is logged before role checks affect anything, and reaching `POST /query` only needs a valid session/API token via `auth.AuthenticateGQL`).
- Requires the node to be running with Debug log level enabled, which is common in non-production, staging, or troubleshooting scenarios and is an explicit, supported configuration (not a misconfiguration bug being exploited — it's a supported logging mode where the code's own redaction promise fails).
- Fully deterministic and repeatable: any nested password field in any JSON body (not just GraphQL) bypasses the filter identically.

### Recommendation
Modify `readSanitizedJSON` (and `redact` for symmetry) to recursively walk nested `map[string]any` and `[]any` structures and redact any key matching `isBlacklisted` at any depth, not just top-level keys. Alternatively, redact known sensitive GraphQL variable paths explicitly, or avoid logging the `variables` field of GraphQL requests altogether at `POST /query`.

### Proof of Concept
Go unit test targeting `readSanitizedJSON` in `core/web/router.go`:
```go
func TestReadSanitizedJSON_NestedGraphQLPassword(t *testing.T) {
    body := `{"query":"mutation createAPIToken($input: CreateAPITokenInput!) { createAPIToken(input: $input) { ... } }","variables":{"input":{"password":"secret123"}}}`
    buf := bytes.NewBufferString(body)
    out, err := readSanitizedJSON(buf)
    require.NoError(t, err)
    // Expect FAIL with current implementation: password leaks unredacted
    assert.NotContains(t, out, "secret123", "plaintext password must not appear in sanitized log output")
}
```
Expected current (buggy) behavior: assertion fails because `out` contains `"secret123"` unredacted, proving the nested password bypasses `isBlacklisted`/`readSanitizedJSON`'s flat traversal.

### Citations

**File:** core/web/router.go (L64-69)
```go
	engine.Use(
		otelgin.Middleware("chainlink-web-routes",
			otelgin.WithTracerProvider(otel.GetTracerProvider())),
		limits.RequestSizeLimiter(config.WebServer().HTTPMaxSize()),
		loggerFunc(app.GetLogger()),
		gin.Recovery(),
```

**File:** core/web/router.go (L95-99)
```go
	api.POST("/query",
		auth.AuthenticateGQL(app.AuthenticationProvider(), app.GetLogger().Named("GQLHandler")),
		loader.Middleware(app),
		graphqlHandler(app),
	)
```

**File:** core/web/router.go (L556-562)
```go
		lggr.Debugw(fmt.Sprintf("%s %s", c.Request.Method, c.Request.URL.Path),
			"method", c.Request.Method,
			"status", c.Writer.Status(),
			"path", c.Request.URL.Path,
			"ginPath", c.FullPath(),
			"query", redact(c.Request.URL.Query()),
			"body", readBody(rdr, lggr),
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
