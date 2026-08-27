Based on my investigation, I could not find the definition body of `isBlacklisted` (only its call sites in `core/web/router.go`), so I cannot confirm which field names are actually redacted. However, the reachable pattern most analogous to the reported "printing secrets to logs" bug is in the request-logging middleware itself.

### Title
Sensitive credentials passed as URL query parameters are logged in plaintext by the request logger - (File: core/web/router.go)

### Summary
The Chainlink node's HTTP request logging middleware logs the full request body and query string for every request, redacting only fields whose *key name* matches an internal blacklist. Multiple unprivileged-but-authenticated API endpoints (e.g. key export) accept highly sensitive values — passwords used to re-encrypt exported private keys — as URL query parameters rather than JSON body fields, and it is not verified that the query-parameter key names used by these endpoints (e.g. `newpassword`) are included in the blacklist checked by `redact()`.

### Finding Description
`loggerFunc` in [1](#0-0)  logs every request's method, path, query string, and body at debug level:
```go
"query", redact(c.Request.URL.Query()),
"body", readBody(rdr, lggr),
```
Both `redact()` and `readSanitizedJSON()` rely on `isBlacklisted(k)` to decide whether to mask a field [2](#0-1) . This is a denylist approach: any key not explicitly enumerated is logged in full.

Key export endpoints send the re-encryption password as a raw query parameter, not a JSON body field, e.g.:
```go
resp, err := cli.HTTP.Post(cli.ctx(), cli.path+"/export/"+ID+"?newpassword="+normalizedPassword, nil)
``` [3](#0-2) 

and server-side:
```go
newPassword := c.Query("newpassword")
bytes, err := kc.ks.Export(keyID, newPassword)
``` [4](#0-3) 

Because this password travels as a URL query parameter (`?newpassword=...`), it is captured by `c.Request.URL.Query()` and passed through `redact()`. If the string `newpassword` (or other query keys used by ETH/OCR/OCR2/P2P/CSA/VRF/Workflow key export commands) is not present in the (unverified) blacklist maintained separately in `isBlacklisted`, this password used to protect exported private-key JSON will be written verbatim into the node's debug logs on every export call.

### Impact Explanation
If the export-encryption password leaks into logs, anyone with read access to the node's debug logs (log aggregation, container logs, log shipping backends) can decrypt any previously or subsequently exported encrypted key JSON blob for that same password, potentially exposing ETH/OCR/OCR2/P2P/CSA/VRF/workflow private key material. This mirrors the reported vulnerability class: sensitive secret material captured via `print`/log statements accessible to anyone with log access.

### Likelihood Explanation
Requires the node operator to have enabled `Log.Level = 'debug'` (the request logger fires at Debug level) and to have exported a key via the standard CLI/API flow — a normal administrative operation. Likelihood is moderate: it depends on the denylist's completeness, which I could not fully verify since the body of `isBlacklisted` was not retrievable in this index.

### Recommendation
- Move all sensitive values (like `newpassword`) out of URL query strings and into the request body, and/or use an allowlist (only log known-safe fields) rather than a denylist for query-string logging.
- Audit `isBlacklisted` to ensure every query/body parameter name used to carry secrets (`newpassword`, `oldpassword`, `password`, etc.) across all controllers is included.
- Avoid logging raw `c.Request.URL.Query()` at all for endpoints that accept secrets via query parameters.

### Proof of Concept
1. Enable `Log.Level = 'debug'` on a running Chainlink node.
2. Call `POST /v2/keys/eth/export/{address}?newpassword=SuperSecretPass123` (as done by `ExportETHKey`/`ExportKey` in [5](#0-4) ).
3. Inspect the node's debug logs produced by `loggerFunc` — if `newpassword` is not in the blacklist, the log line will contain `query=...newpassword=SuperSecretPass123...` in plaintext, exposing the key-encryption password.

Note: I was unable to locate the actual implementation/contents of `isBlacklisted` in the indexed codebase (only its 3 call sites in `core/web/router.go` were found), so I cannot definitively confirm whether `newpassword` is or isn't currently redacted — this should be verified directly in a full checkout of the repository, since index size limits may have excluded this function body.

### Citations

**File:** core/web/router.go (L534-568)
```go
func loggerFunc(lggr logger.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		buf, err := io.ReadAll(c.Request.Body)
		if err != nil {
			lggr.Error("Web request log error: ", err.Error())
			// Implicitly relies on limits.RequestSizeLimiter
			// overriding of c.Request.Body to abort gin's Context
			// inside io.ReadAll.
			// Functions as we would like, but horrible from an architecture
			// and design pattern perspective.
			if !c.IsAborted() {
				c.AbortWithStatus(http.StatusBadRequest)
			}
			return
		}
		rdr := bytes.NewBuffer(buf)
		c.Request.Body = io.NopCloser(bytes.NewBuffer(buf))

		start := time.Now()
		c.Next()
		end := time.Now()

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

**File:** core/cmd/keys_commands.go (L222-233)
```go
	filepath := c.String("output")
	if len(filepath) == 0 {
		return cli.errorOut(errors.New("Must specify --output/-o flag"))
	}

	ID := c.Args().Get(0)

	normalizedPassword := normalizePassword(string(newPassword))
	resp, err := cli.HTTP.Post(cli.ctx(), cli.path+"/export/"+ID+"?newpassword="+normalizedPassword, nil)
	if err != nil {
		return cli.errorOut(errors.Wrap(err, "Could not make HTTP request"))
	}
```

**File:** core/web/keys_controller.go (L138-147)
```go
func (kc *keysController[K, R]) Export(c *gin.Context) {
	defer kc.lggr.ErrorIfFn(c.Request.Body.Close, "Error closing Export request body")

	keyID := c.Param("ID")
	newPassword := c.Query("newpassword")
	bytes, err := kc.ks.Export(keyID, newPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
```

**File:** core/cmd/eth_keys_commands.go (L328-336)
```go
	address := c.Args().Get(0)
	exportUrl := url.URL{
		Path: "/v2/keys/evm/export/" + address,
	}
	query := exportUrl.Query()
	query.Set("newpassword", strings.TrimSpace(string(newPassword)))

	exportUrl.RawQuery = query.Encode()
	resp, err := s.HTTP.Post(s.ctx(), exportUrl.String(), nil)
```
