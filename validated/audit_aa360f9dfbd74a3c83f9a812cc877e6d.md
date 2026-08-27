### Title
Raw per-node aggregation responses leaked to user in `RequestTimeoutError` payload - ([File: core/services/gateway/handlers/vault/handler.go])

### Finding Description
In `removeExpiredRequests`, when a user's vault request times out before reaching quorum, the handler iterates the raw `copiedResponses()` map (keyed by node identity, holding each node's decoded `jsonrpc.Response`) and formats them verbatim with `fmt.Fprintf(&nodeResponses, "%s ---::: %v ...", nodeKey, nodeResponse)` [1](#0-0) . The resulting `nodeResponsesStr` is embedded both in the wrapped Go `error` text and passed directly as the raw response body (`[]byte(nodeResponsesStr)`) to `h.errorResponse(er.req, api.RequestTimeoutError, ..., []byte(nodeResponsesStr))`, which is then delivered to the requesting user via `h.sendResponse` [2](#0-1) .

This means whatever content individual DON nodes returned for this request — including each node's raw `jsonrpc.Response` (which can carry node-specific `Error.Message` strings or other implementation-specific fields) — is concatenated and returned unfiltered as the `data` field of the timeout error sent back to the calling user. There is no redaction, allow-listing, or sanitization step between the per-node aggregation state and the outbound user-facing payload.

### Impact Explanation
This falls under information disclosure of internal aggregation/node state to an unprivileged caller. The impact is bounded by what individual nodes put into their JSON-RPC error/response objects for a given request; if any node's error text includes internal diagnostic detail (stack traces, backend error strings, partial state) that was not intended to reach end users, that content is now attacker-observable simply by causing a request to time out without quorum (e.g., racing the timeout, or crafting a request where nodes disagree/error, preventing quorum). This is a confidentiality/information-leak issue (SECRET_CONFINEMENT invariant violation) rather than an auth-bypass or fund-movement issue — scoped impact is disclosure of internal error/response contents, not disclosure of actual secret material (vault secrets themselves are not asserted to appear here, since node responses for e.g. SecretsList would not normally embed decrypted secret values, but internal metadata/error strings could still leak).

### Likelihood Explanation
No privilege is required: any unauthenticated/authorized gateway client that can submit a vault JSON-RPC request can trigger this by causing (or waiting for) a quorum failure within the `requestTimeout` window — e.g., by racing the timeout or exploiting transient node disagreement. This is fully attacker-triggerable and repeatable, requiring only normal API access to the gateway vault handler.

### Recommendation
Do not include raw per-node `jsonrpc.Response` contents in user-facing error responses. Log the full `nodeResponses` map server-side (e.g., via `h.lggr.Debugw`) for operational diagnostics, but return a generic, sanitized message to the user (e.g., "request expired without reaching quorum") with no per-node error bodies in the `data`/`RawResponse` field of `errorResponse`.

### Proof of Concept
1. Add a unit test in `core/services/gateway/handlers/vault/handler_test.go` that:
   - Constructs a `handler` with a short `requestTimeout`.
   - Manually inserts an `activeRequest` into `h.activeRequests` with `copiedResponses()` returning a map containing at least one node's `jsonrpc.Response` whose `Error.Message` contains a sentinel string such as `"INTERNAL: db connection to secretsstore failed at host X"`.
   - Advances the mock clock past `requestTimeout` and invokes `h.removeExpiredRequests(ctx)`.
   - Captures the `UserCallbackPayload` sent via the callback/`sendResponse` path.
   - Asserts that `payload.RawResponse` (or its decoded `data` field) does NOT contain the sentinel string, and that only a generic timeout message is present.
2. Run the test against current code to confirm it fails (sentinel string is present in the response), demonstrating the leak.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L381-387)
```go
	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		var nodeResponses strings.Builder
		for nodeKey, nodeResponse := range responses {
			_, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
		}
		nodeResponsesStr := nodeResponses.String()
```

**File:** core/services/gateway/handlers/vault/handler.go (L388-391)
```go
		err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
		if err != nil {
			h.lggr.Errorw("error sending response to user", "requestID", er.req.ID, "error", err)
		}
```
