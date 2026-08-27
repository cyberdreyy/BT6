## Analysis

The Futureswap bug is a **replay-slot griefing / request-ID reservation DoS**: an attacker submits someone else's authorization material first, occupying an "interaction number" and causing the victim's legitimate follow-up submission with the same identifier to be rejected, resulting in a denial-of-service against the honest user.

I found a direct structural analog in the Chainlink gateway's `confidentialrelay` handler.

### Title
Unauthenticated request-ID reservation in the Confidential Relay gateway handler enables denial-of-service against legitimate users - (File: `core/services/gateway/handlers/confidentialrelay/handler.go`)

### Summary
`ConfidentialRelayHandler.HandleJSONRPCUserMessage` reserves a client-supplied `req.ID` slot in the shared `activeRequests` map before performing any authentication or authorization check, and without namespacing the ID by an authenticated owner. Any caller who can reach the gateway's public endpoint for this handler can pre-empt (reserve) an arbitrary request ID, causing a legitimate user's subsequent request using that same ID to be permanently rejected with "request ID already exists" until the attacker's bogus entry expires or is cleared.

### Finding Description
In `HandleJSONRPCUserMessage`, the only validation performed is that `req.ID` is non-empty and under 200 characters, after which `newActiveRequest` is called directly: [1](#0-0) 

`newActiveRequest` unconditionally reserves the raw `req.ID` in the shared `h.activeRequests` map and rejects any later request bearing the same ID: [2](#0-1) 

Unlike this handler, the sibling `vault` handler in the same gateway package performs authorization **before** reserving the request slot, and namespaces the reserved ID by the authenticated owner (`owner + separator + requestID`) so that two different, unauthenticated parties cannot collide on the same raw ID: [3](#0-2) [4](#0-3) 

The `confidentialrelay` handler has no analogous `authorizer`/`AuthorizedOwner` field or JWT/allowlist check anywhere in its `handler` struct or `HandleJSONRPCUserMessage`/`newActiveRequest` path — there is no gate between "any client that can call the gateway's ProcessRequest entrypoint" and "this handler reserving a global request-ID slot." The gateway's dispatch loop (`gateway.go: ProcessRequest`) routes requests to handlers purely by service name/method, performing no cross-cutting caller authentication itself: [5](#0-4) 

The handler's own test suite confirms the exact mechanic: the second caller using the same `req.ID` is rejected outright, with no ownership check involved: [6](#0-5) 

This mirrors the Futureswap root cause precisely: **reservation of a scarce, shared identifier happens before/without verifying the caller is authorized to act on it**, so an unprivileged party can consume the reservation and force the real requester's message to be rejected.

### Impact Explanation
An unprivileged, unauthenticated caller can deny service to a legitimate workflow/capability request by submitting a request with the same `ID` first. Because activation happens per-ID and the map is process-global for the DON's confidential relay handler, this is a low-cost, repeatable griefing vector: the attacker's bogus request will itself fail (no valid params/signed responses), but it occupies the ID for up to `RequestTimeoutSec` (default 30s, see `defaultRequestTimeoutSec`), blocking the legitimate request from ever completing during that window, and can be repeated to keep blocking retries if the victim reuses IDs (e.g., deterministic workflow/step IDs).

### Likelihood Explanation
Exploitability depends on the attacker being able to guess or observe the victim's `req.ID` and having access to submit JSON-RPC messages to the confidential relay method on the gateway. If request IDs are predictable/deterministic (e.g., derived from workflow execution IDs, step numbers, or otherwise observable identifiers as is common in orchestrated workflow systems), this is straightforward to exploit without any credentials, since the handler performs no authentication at all before reserving the slot.

### Recommendation
Mirror the `vault` handler's pattern: authenticate/authorize the caller before reserving an entry in `activeRequests`, and namespace the reserved key by the authenticated identity (e.g., `owner + separator + req.ID`) rather than the raw client-supplied ID, so that two different unauthenticated/unauthorized parties cannot collide on the same identifier.

### Proof of Concept
1. Attacker sends a JSON-RPC request to the confidential relay gateway method (`MethodSecretsGet` or `MethodCapabilityExec`) with `ID = "victim-id"` and arbitrary/garbage params.
2. `newActiveRequest` succeeds and reserves `"victim-id"` in `h.activeRequests` (per `core/services/gateway/handlers/confidentialrelay/handler.go` lines 368-383).
3. The victim's genuine request, also using `ID = "victim-id"`, is submitted shortly after.
4. `newActiveRequest` returns `"request ID already exists: victim-id"` for the victim's real request, which is returned to the gateway caller as a handler error — exactly the behavior exercised in `TestConfidentialRelayHandler_DuplicateRequestID`.
5. The victim's legitimate request never reaches the DON; it must wait out the attacker's reservation timeout and retry, and the attacker can repeat this cheaply.

### Citations

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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L435-450)
```go
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
	if activeRequestErr != nil {
		return activeRequestErr
	}
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L294-306)
```go
		req := jsonrpc.Request[json.RawMessage]{
			ID:     "1",
			Method: vaulttypes.MethodSecretsCreate,
			Params: &rawPayload,
		}

		err = h.HandleJSONRPCUserMessage(t.Context(), req, common.NewCallback())
		require.NoError(t, err)

		require.NotNil(t, forwarded.Params)
		var forwardedCreateRequest vaultcommon.CreateSecretsRequest
		require.NoError(t, json.Unmarshal(*forwarded.Params, &forwardedCreateRequest))
		require.Equal(t, "0xworkflow"+vaulttypes.RequestIDSeparator+"1", forwardedCreateRequest.RequestId)
```

**File:** core/services/gateway/gateway.go (L264-276)
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
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```
