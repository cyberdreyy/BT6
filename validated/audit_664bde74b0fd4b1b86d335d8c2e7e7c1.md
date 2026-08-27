### Title
Confidential Relay gateway handler allows request-ID squatting/frontrunning, letting an unauthenticated actor block a legitimate user's request - (File: core/services/gateway/handlers/confidentialrelay/handler.go)

### Summary
The `confidentialrelay` gateway handler keys all in-flight requests by a client-supplied JSON-RPC `id` in a single shared map, with no per-caller namespacing and no authorization gate before that check. Any actor who can reach the gateway's public HTTP endpoint can submit a request using an ID they expect (or observe) a victim to use, causing the victim's subsequent request with the same ID to be permanently rejected — the same "user-selected unique identifier can be squatted by an unprivileged third party" bug class as the reported Solidity `loanId` frontrunning issue.

### Finding Description
`HandleJSONRPCUserMessage` accepts the JSON-RPC `req.ID` directly from the untrusted caller, only validating that it is non-empty and ≤200 chars, and immediately calls `newActiveRequest`: [1](#0-0) 

`newActiveRequest` stores the request in `h.activeRequests` keyed solely by the raw, attacker-controlled `req.ID`. If an entry with that ID already exists, the call is rejected outright with no owner/auth check: [2](#0-1) 

This is reachable from any unauthenticated client that can send a JSON-RPC request to the internet-facing gateway, dispatched through `gateway.ProcessRequest`, which routes based only on decoded request/service name — no per-request identity binding is enforced before it reaches the handler: [3](#0-2) 

Compare this with the sibling `vault` handler, which routes every request through `GatewayVaultRequestProcessor.authorizeAndStamp` — this authenticates the caller and rewrites `req.ID` to `authorizedOwner + separator + originalID` *before* the active-request map is consulted, so collisions are scoped per authenticated owner and cannot be caused by a stranger: [4](#0-3) 

The `confidentialrelay` handler has no equivalent authorization/ID-namespacing step before `newActiveRequest`, so the ID space is global across all unauthenticated callers of that DON's confidential relay method.

### Impact Explanation
An attacker with no privileges can grief legitimate users by pre-registering (or racing) the same `req.ID` that a victim's client is expected to use, causing the victim's legitimate confidential-relay request to be rejected with `"request ID already exists"`. This is a direct denial-of-service/griefing analog to the reported `loanId` frontrunning bug: a user-selected, unauthenticated identifier used as a global uniqueness key that any unprivileged actor can duplicate to block others, exactly the class the external report flags (`Griefing`, no profit motive needed).

### Likelihood Explanation
Exploitation requires only sending a JSON-RPC request with a chosen `id` to the public gateway HTTP endpoint before the victim's real request arrives — no credentials, signing, or allowlisting is required for this handler's ID uniqueness check. If clients use predictable or enumerable IDs (e.g., incrementing counters, workflow IDs, or IDs learned from other public signals), an attacker can reliably squat on them; even with random IDs, an attacker monitoring/observing outgoing traffic (e.g., via a race against gateway ingestion) can attempt collisions repeatedly against active users.

### Recommendation
Namespace `activeRequests` keys by authenticated caller/session identity (mirroring the `vault` handler's `authorizedOwner + RequestIDSeparator + originalID` prefixing) before performing the uniqueness check, so that ID collisions can only occur within a single authenticated caller's own request stream rather than across all callers.

### Proof of Concept
1. Attacker sends a JSON-RPC request to the gateway's confidential-relay method with `id = "X"` (no authentication required beyond reaching the endpoint), which succeeds and populates `activeRequests["X"]`.
2. Victim's legitimate client, expecting to use `id = "X"` for its own request, sends its request.
3. `newActiveRequest` finds `h.activeRequests["X"] != nil` and returns `errors.New("request ID already exists: X")`, and `HandleJSONRPCUserMessage` propagates this error, per: [5](#0-4) 
This matches the existing unit test demonstrating the collision behavior, confirming the mechanism (only missing the cross-actor/unauthenticated angle): [6](#0-5)

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

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-248)
```go
func (p *GatewayVaultRequestProcessor) authorizeAndStamp(
	ctx context.Context,
	req *jsonrpc.Request[json.RawMessage],
	stamp func(prefixedRequestID string) error,
) (*AuthorizedGatewayVaultRequest, error) {
	incomingOwner := ""
	if idx := strings.Index(req.ID, vaulttypes.RequestIDSeparator); idx != -1 {
		incomingOwner = req.ID[:idx]
	}

	p.lggr.Debugw("authorizing gateway vault request", "method", req.Method, "requestID", req.ID)
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}

	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
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
