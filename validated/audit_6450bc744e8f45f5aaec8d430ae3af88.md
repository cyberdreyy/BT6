### Title
Full response payload (including vault secret data) logged unredacted in gateway.ProcessRequest debug log - ([File: core/services/gateway/gateway.go])

### Summary
`gateway.ProcessRequest` logs the entire handler `response` object — including its `RawResponse` field — via `g.lggr.Debugw("received response from handler", "handler", handlerKey, "response", response, "requestID", jsonRequest.ID)` for every request processed by the gateway, with no redaction of payload contents. Since `RawResponse` carries the raw JSON-RPC response bytes produced by handlers (e.g., the vault handler for `MethodSecretsList`/`MethodSecretsCreate`), any secret material returned to a legitimate low-privilege caller is also passed unfiltered into the structured logger.

### Finding Description
In `core/services/gateway/gateway.go`, `ProcessRequest` dispatches the decoded request to the appropriate `handlers.Handler` (including the vault handler via `serviceToMultiHandler`), waits for the response via `callback.Wait(ctx)`, and then executes: [1](#0-0) 

The `response` object here is the full response struct (containing `RawResponse []byte` and `ErrorCode`), and it is passed directly as a structured log field with no field-level redaction, truncation, or allow-list filtering. No `MarshalLogObject`/`LogValue`/`String()` override was found on this response type restricting what gets serialized into the log — the struct's fields, including `RawResponse`, are logged as-is. For vault operations (`MethodSecretsList`, `MethodSecretsCreate`) that legitimately return secret-bearing payloads to the requesting caller over the gateway's normal RPC response path, that same raw payload — potentially containing plaintext secret values or key material returned to the caller — flows into this `Debugw` call before being returned via `response.RawResponse` to the HTTP caller. There is no redaction step between the handler producing the response and this log statement, so the log sink receives the identical bytes as the wire response.

### Impact Explanation
This is a secret-disclosure-via-logs pattern: sensitive response payloads (vault secrets, key material) that should be confined to the authorized caller's response channel are also duplicated into the node's log stream at Debug level. If Debug logging is enabled in an environment (a supported, non-default but legitimate operational configuration) or logs are shipped to a lower-trust aggregation system, this creates a secondary disclosure channel for secrets that were only intended for the single authorized recipient of the gateway response, undermining the "secret confinement" invariant even without any attacker privilege escalation being required — the requester is fully authorized to receive the secret in the RPC response itself, but the *log copy* is exposed to a different set of readers/consumers than the intended response channel.

### Likelihood Explanation
No attacker privilege beyond normal use of the vault/gateway API (any caller entitled to create/list a secret) is needed to cause the sensitive payload to transit this log call — the mere act of a legitimate `MethodSecretsCreate`/`MethodSecretsList` round trip triggers it. The only variable condition is whether Debug-level logging is enabled, which is an operator/config choice, not an application-logic gate; the code contains no guard preventing sensitive data from reaching this log statement regardless of log level configuration.

### Recommendation
Redact or omit the payload from this log line: log only non-sensitive metadata (`handlerKey`, `requestID`, `response.ErrorCode`, response size) and avoid passing the full `response` struct (or its `RawResponse` bytes) to `Debugw`. If response content must be logged for diagnostics, apply method-aware redaction (e.g., strip/replace vault secret fields) before logging, or gate any raw-payload logging behind an explicit, clearly-documented debug flag that operators are warned discloses secrets.

### Proof of Concept
Go unit test plan for `core/services/gateway/gateway_test.go`:
1. Construct a `gateway` with a mocked handler whose `HandleJSONRPCUserMessage` populates the callback with a `Response{RawResponse: <JSON containing a known plaintext secret string>, ErrorCode: api.NoError}` (simulating a successful `MethodSecretsCreate`/`MethodSecretsList` vault response).
2. Attach an observed/mock `logger.Logger` (e.g., `logger.TestObserved(t, zapcore.DebugLevel)`) to the gateway.
3. Call `g.ProcessRequest(ctx, rawRequest, auth)` for a vault secrets request.
4. Assert on the captured log entries: search all fields/messages emitted at `Debugw("received response from handler", ...)` for the known plaintext secret substring.
5. Expected (failing) assertion under current code: the secret substring **is** present in the logged fields, demonstrating the raw payload transits the log call unredacted. After the fix, assert the substring is **absent** from all log output while metadata fields (`handlerKey`, `requestID`, error code) are still present.

### Citations

**File:** core/services/gateway/gateway.go (L278-291)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
	g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.ErrorCode.String(), duration)
	g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.ErrorCode.String())

	g.lggr.Debugw("received response from handler", "handler", handlerKey, "response", response, "requestID", jsonRequest.ID)
	promRequest.WithLabelValues(response.ErrorCode.String()).Inc()
	return response.RawResponse, api.ToHttpErrorCode(response.ErrorCode)
```
