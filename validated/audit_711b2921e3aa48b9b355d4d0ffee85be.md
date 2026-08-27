### Title
Distinguishable JSON-RPC error messages in vault gateway authorization leak internal allowlist state ("not allowlisted" vs "expired" vs "workflowRegistrySyncer is nil") - ([File: core/capabilities/vault/gw_handler.go])

### Summary
`GatewayHandler.gatewayErrorResponse` forwards the raw `err.Error()` text from the authorization pipeline directly to the requesting client in the JSON-RPC `Error.Message` field. Because `allowListBasedAuth.AuthorizeRequest` returns three distinct, human-readable error strings for three different internal states (never allowlisted, allowlisted-but-expired, and syncer-is-nil), an unauthenticated/unprivileged gateway client can distinguish these states from the response text.

### Finding Description
`allowListBasedAuth.AuthorizeRequest` in `core/capabilities/vault/allow_list_based_auth.go` returns three different error strings depending on internal state:
- `"internal error: workflowRegistrySyncer is nil"` [1](#0-0) 
- `"request not allowlisted"` when no matching digest is found [2](#0-1) 
- `"request authorization expired"` when a matching digest exists but its expiry has passed [3](#0-2) 

These errors bubble up through `GatewayVaultRequestProcessor.authorizeAndStamp`, which wraps them with `fmt.Errorf("request not authorized: %w", err)` but preserves the original distinguishing text [4](#0-3) . The wrapped error is then passed unmodified to `GatewayHandler.gatewayErrorResponse`, which (since it is not an `InvalidVaultParamsError`) routes it to `h.errorResponse` with `api.HandlerError` and sets `Message: err.Error()` verbatim [5](#0-4) [6](#0-5) . This response is sent directly back over the gateway to the requester via `SendToGateway` — no redaction or generic-message normalization occurs anywhere in this path.

An attacker who knows (or can compute/guess) the exact JSON-RPC request fields that produce a specific `req.Digest()` (method, params, and owner-derived request ID — none of which are secret, since digests are derived from request content, not a secret key) can send that exact request and observe the returned message: `"...request not allowlisted"` means that digest was never authorized, `"...request authorization expired"` means it was authorized in the past but is now stale, and `"...workflowRegistrySyncer is nil"` reveals a node-internal misconfiguration/bootstrapping state. This constitutes a genuine error-message oracle distinguishing "never valid" from "was valid, now expired," leaking authorization history/timing metadata that should not be observable by an unprivileged caller.

### Impact Explanation
This is an information-disclosure issue: an external, unauthenticated gateway caller can learn whether a specific vault action (identified by its request digest) was ever allowlisted and, if so, that its window has since expired, versus never having existed. It also discloses an internal service fault state (`workflowRegistrySyncer is nil`) to external clients, which is implementation detail that should stay server-side. It does not by itself grant privilege escalation, secret access, or fund movement — no secrets, keys, or another owner's protected data are returned — so this maps to a low-severity "information disclosure / verbose error message" class rather than authentication bypass or credential exposure.

### Likelihood Explanation
Exploitation requires no privilege: any client able to send JSON-RPC requests through the gateway (an "unauthenticated client...or any address sending signed gateway requests" per the threat model) can trigger this by crafting a request whose digest matches a state they want to probe. The main practical constraint is that the attacker must already know/construct the exact request content producing the digest of interest, which limits blind cross-user enumeration in practice but does not eliminate the oracle for the attacker's own or guessable requests. The path is reachable directly from `HandleGatewayMessage` on every `MethodSecretsCreate/Update/Delete/List` call, so it is easily repeatable with no rate limiting on this particular text differentiation.

### Recommendation
Normalize all authorization-failure error messages returned to the gateway client into a single generic message such as `"not authorized"` (using a fixed `api.ErrorCode`), while keeping the detailed internal error (allowlist miss vs expired vs nil syncer) only in the server-side structured log via `reqLggr`/`h.lggr`. This should be done in `GatewayHandler.gatewayErrorResponse` (or by wrapping the authorizer error into a sentinel error type before it reaches the response layer) rather than passing `err.Error()` through to `errorResponse`.

### Proof of Concept
Table-driven Go test in `core/capabilities/vault/gw_handler_test.go` style:
1. Construct a `GatewayHandler` with a mock `Authorizer`/`allowListBasedAuth` (or stub `workflowRegistrySyncer`) configured to return, in turn: (a) nil `workflowRegistrySyncer` state, (b) a digest with no matching allowlist entry, (c) a digest matching an allowlist entry with `ExpiryTimestamp` in the past.
2. For each case, call `HandleGatewayMessage` with a `MethodSecretsDelete`/`MethodSecretsList` request and capture the `jsonrpc.Response.Error` sent via the mock `gatewayConnector.SendToGateway`.
3. Assert that in all three cases `Error.Code` is identical and `Error.Message` is exactly `"not authorized"` (or another single generic string), rather than the current distinct strings `"...workflowRegistrySyncer is nil"`, `"...request not allowlisted"`, and `"...request authorization expired"`.
4. Currently this assertion fails, proving the messages differ and leak internal state — validating the oracle exists.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L47-50)
```go
	if r.workflowRegistrySyncer == nil {
		r.lggr.Errorw("AllowListBasedAuth workflowRegistrySyncer is nil", "method", req.Method, "requestID", req.ID)
		return nil, errors.New("internal error: workflowRegistrySyncer is nil")
	}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L55-62)
```go
	if allowlistedRequest == nil {
		r.lggr.Debugw("AllowListBasedAuth request digest not allowlisted",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"allowedRequestsStrs", allowedRequestsStrs)
		return nil, errors.New("request not allowlisted")
	}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L64-68)
```go
	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L233-238)
```go
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}
```

**File:** core/capabilities/vault/gw_handler.go (L263-273)
```go
func (h *GatewayHandler) gatewayErrorResponse(
	ctx context.Context,
	gatewayID string,
	req *jsonrpc.Request[json.RawMessage],
	err error,
) *jsonrpc.Response[json.RawMessage] {
	if IsInvalidVaultParamsError(err) {
		return h.errorResponse(ctx, gatewayID, req, api.InvalidParamsError, errors.New("invalid params error: "+err.Error()))
	}
	return h.errorResponse(ctx, gatewayID, req, api.HandlerError, err)
}
```

**File:** core/capabilities/vault/gw_handler.go (L388-410)
```go
func (h *GatewayHandler) errorResponse(
	ctx context.Context,
	gatewayID string,
	req *jsonrpc.Request[json.RawMessage],
	errorCode api.ErrorCode,
	err error,
) *jsonrpc.Response[json.RawMessage] {
	h.requestLogger(req, gatewayID).Errorw("gateway handler error response", "errorCode", errorCode, "error", err)
	h.metrics.requestInternalError.Add(ctx, 1, metric.WithAttributes(
		attribute.String("gateway_id", gatewayID),
		attribute.String("error", errorCode.String()),
	))

	return &jsonrpc.Response[json.RawMessage]{
		Version: jsonrpc.JsonRpcVersion,
		ID:      req.ID,
		Method:  req.Method,
		Error: &jsonrpc.WireError{
			Code:    api.ToJSONRPCErrorCode(errorCode),
			Message: err.Error(),
		},
	}
}
```
