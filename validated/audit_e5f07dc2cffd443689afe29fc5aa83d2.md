### Title
Confidential relay gateway handler forwards requests to all DON nodes without any sender/JWT authentication check - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`gateway.ProcessRequest` only calls `msg.Validate()` (which recovers and checks the message signer) on the legacy DON-ID routing path; for new-style JSON-RPC requests it dispatches directly to `Handler.HandleJSONRPCUserMessage` with no gateway-level sender check, deferring all authentication to the handler. The `confidentialrelay` handler's `HandleJSONRPCUserMessage` performs only request-ID length validation and then immediately fans the request out to DON nodes via `fanOutToNodes`, with no call to any signature/JWT verification or authorized-key check before contacting nodes.

### Finding Description
In `core/services/gateway/gateway.go`, `ProcessRequest` branches on whether the decoded message has a `DonId`: the legacy path sets `isLegacyRequest = true` and calls `msg.Validate()` before dispatch [1](#0-0) , but the new-style JSON-RPC path skips straight to `h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)` with no equivalent gateway-level authentication step [2](#0-1) . The `Handler` interface documents that each handler "should validate the message" itself [3](#0-2) , so authentication is explicitly a handler responsibility, not a gateway-enforced invariant.

Looking at the handlers that implement `HandleJSONRPCUserMessage`:
- `vault/handler.go` calls `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)` to authorize the caller before creating an active request or contacting nodes [4](#0-3) .
- `capabilities/v2/workflow_metadata_handler.go`'s `Authorize` verifies a JWT (`utils.VerifyRequestJWT`), checks for replay via `jwtCache`, and cross-checks the recovered signer against a per-workflow `authorizedKeys` allowlist before returning success [5](#0-4) .
- `confidentialrelay/handler.go`'s `HandleJSONRPCUserMessage`, by contrast, only validates `req.ID` length/emptiness, then calls `h.newActiveRequest(req, callback)` and `h.fanOutToNodes(ctx, l, ar)` — there is no call to any signature verification, JWT check, or sender allowlist anywhere in this function or in the surrounding file (confirmed via search: no `Auth` references exist in the entire `confidentialrelay` package) [6](#0-5) .

This means any caller who can reach the gateway's user-facing HTTP endpoint can submit a `confidentialrelay`-routed JSON-RPC request (`MethodSecretsGet` or `MethodCapabilityExec`) with no `Auth`/JWT at all, or a garbage `Auth` value, and the gateway will still fan it out to every DON node via `don.SendToNode`, since nothing in the gateway or handler path rejects it before that fan-out.

### Impact Explanation
This matches "unauthorized job run" / "request impersonation" impact classes: an unauthenticated attacker can trigger DON-wide dispatch of confidential-relay requests (e.g. `MethodSecretsGet`, `MethodCapabilityExec`) that should be gated by sender identity. Whether this results in actual secret disclosure or unauthorized capability execution depends on whether the receiving nodes perform their own independent authentication of the forwarded request — I could not verify node-side validation behavior in the DON/oracle process from what's indexed, so the confirmed exploitable impact at the gateway layer is unauthorized DON fan-out/dispatch of any-content confidential-relay requests without gateway-side sender authentication, which is a real gap relative to the `vault` and `capabilities/v2` handlers that do enforce authorization before contacting nodes.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only unauthenticated HTTP access to the gateway's user API and knowledge of the `confidentialrelay` service name/method (`secrets.get`/`capability.exec`), both of which are effectively public routing metadata. No signature, credential, or JWT is required to reach `fanOutToNodes`, making this trivially and repeatably reproducible.

### Recommendation
Add an explicit authentication/authorization step to `confidentialrelay.handler.HandleJSONRPCUserMessage` analogous to `vault`'s `requestProcessor.ProcessRequest` or `WorkflowMetadataHandler.Authorize` — verify `req.Auth` (JWT or signature), recover and check the signer against a DON-level or per-resource allowlist, and reject the request before calling `newActiveRequest`/`fanOutToNodes`. Additionally, consider hardening `gateway.ProcessRequest` so it enforces a baseline sanity check (e.g., that `Auth` is well-formed) uniformly for the JSON-RPC path rather than leaving 100% of enforcement to individual handlers.

### Proof of Concept
Go handler-level test plan (in `core/services/gateway/handlers/confidentialrelay/handler_test.go`):
1. Construct a `handler` with a mock `DON` where `SendToNode` is asserted `NotCalled` or set up to fail the test if invoked.
2. Build a `jsonrpc.Request[json.RawMessage]{ID: "1", Method: MethodSecretsGet, Auth: ""}` (empty/no auth) and call `h.HandleJSONRPCUserMessage(ctx, req, callback)`.
3. Assert the call either returns an error before fan-out, or the callback receives an unauthorized error response, and assert `don.AssertNotCalled(t, "SendToNode", ...)`.
4. Repeat with `Auth` set to a syntactically-valid-but-unrelated/garbage JWT string and assert the same rejection.
5. Contrast with a companion test on `vault/handler.go` and `workflow_metadata_handler.go` showing that equivalent no-auth/invalid-auth requests are rejected by `requestProcessor.ProcessRequest` / `Authorize` respectively before any `don.SendToNode` call — demonstrating the confidential relay handler is the outlier lacking this check.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}
```

**File:** core/services/gateway/gateway.go (L264-273)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/handler.go (L44-47)
```go
	// Handlers should not make any assumptions about goroutines calling HandleNodeMessage.
	// should be non-blocking
	// should validate the message inside the response
	HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error
```

**File:** core/services/gateway/handlers/vault/handler.go (L431-447)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```
