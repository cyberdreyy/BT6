## Analog Found

### Title
Cross-user response hijacking via unscoped, attacker-controlled MessageId in legacy Gateway capabilities handler - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The C4 report describes an attacker exploiting the fact that a resource identifier (`_cidNFTID`) used to route a follow-up privileged operation is not bound to the specific actor/transaction that is supposed to own it — an attacker can race to claim or collide with that identifier and hijack the follow-up operation intended for the victim. The Chainlink Gateway's legacy capabilities handler has the same root-cause shape: a fully client-controlled, unscoped identifier (`MessageId`) is used as the sole key to route an asynchronous DON response back to a waiting caller, with no per-caller/session binding and no collision check, allowing one unprivileged caller to hijack (or clobber) the response destined for another caller.

### Finding Description
Every legacy user request to the gateway derives its internal `MessageId` directly from the attacker-controlled JSON-RPC `id` field: [1](#0-0) 

`gateway.ProcessRequest` only validates that this ID is `<= 200` characters — it performs no uniqueness or ownership binding: [2](#0-1) 

The legacy capabilities handler then stores the caller's callback in a handler-wide map keyed **only** by this attacker-controlled `MessageId`, with an unconditional overwrite and no check for an existing in-flight entry: [3](#0-2) 

When a DON node later replies, the response is looked up and delivered by that same bare `MessageId`, and only the **first** match is honored: [4](#0-3) 

Because two different, unrelated HTTP callers can each choose the same `MessageId` (there is nothing preventing it — it's just a JSON-RPC request `id` string), whichever caller's `HandleLegacyUserMessage` call registers second silently overwrites the first caller's `savedCallbacks` entry. The subsequent DON response for that `MessageId` is then delivered to whichever callback is currently registered — potentially the wrong caller. This is structurally identical to the CID NFT bug: an identifier that is supposed to uniquely tie a follow-up action back to its rightful initiator (`_cidNFTID` in the report vs. `MessageId` here) is not actually bound to the initiator, and a second, unprivileged party racing to use/reuse that identifier can redirect the outcome to themselves.

Notably, this exact class of bug has already been fixed in two sibling code paths in the same codebase, confirming this is a known anti-pattern the team defends against elsewhere but missed here:
- The vault gateway flow prefixes every ID with the cryptographically authorized owner before using it as a lookup/storage key: [5](#0-4) 
- The newer v2 HTTP trigger handler explicitly rejects a request whose ID is already in flight instead of silently overwriting: [6](#0-5) 

### Impact Explanation
An unprivileged HTTP caller of the gateway (no allowlist/auth is required to reach `HandleLegacyUserMessage` — `// TODO: apply allowlist and rate-limiting here` per [7](#0-6) ) can:
1. Cause another caller's DON response (which may include workflow trigger output/result data) to be delivered to the attacker's own connection by colliding on `MessageId`, resulting in cross-user response confusion/data leakage.
2. Silently drop/DoS a victim's in-flight request by overwriting the map entry, causing the victim's callback to never receive a response (until timeout).

### Likelihood Explanation
The `MessageId` is a plain client-chosen string with no entropy requirement, no per-session/per-caller namespace, and the map is shared across all callers of the handler. Any unprivileged client reaching this endpoint can trivially pick colliding IDs (e.g. flood common/sequential/short IDs, or simply reuse an ID after observing another party's traffic pattern), making exploitation straightforward, requiring no special privileges — only network access to the gateway's legacy JSON-RPC endpoint.

### Recommendation
Bind `savedCallbacks` entries to a namespace that is cryptographically tied to the caller (as already done in the vault flow via `authorizedOwner + RequestIDSeparator + originalRequestID`), and/or add the same in-flight duplicate-ID rejection used by the v2 `httpTriggerHandler.setupCallback` (return a conflict error instead of overwriting).

### Proof of Concept
1. Attacker sends a legacy JSON-RPC request to the gateway's public endpoint with `id: "X"`, `method: "web_api_trigger"`, targeting the same DON as an expected/observed victim request.
2. Victim independently sends a legitimate request also using `id: "X"` (predictable/common ID, or attacker races immediately after observing it).
3. `HandleLegacyUserMessage` for the second-arriving request overwrites `h.savedCallbacks["X"]` [8](#0-7) , discarding the first caller's registered callback.
4. When the DON node responds for `MessageId "X"`, `handleWebAPITriggerMessage` delivers the response to whichever callback is currently stored [9](#0-8)  — the attacker receives the victim's response (or the victim's request is left hanging), depending on race ordering.

### Citations

**File:** core/services/gateway/api/jsonrpccodec.go (L24-33)
```go
func (*JsonRPCCodec) DecodeJSONRequest(request jsonrpc2.Request[json.RawMessage]) (*Message, error) {
	var msg Message
	err := json.Unmarshal(*request.Params, &msg)
	if err != nil {
		return nil, err
	}
	msg.Body.MessageId = request.ID
	msg.Body.Method = request.Method
	return &msg, nil
}
```

**File:** core/services/gateway/gateway.go (L218-231)
```go
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L240-248)
```go
	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```
