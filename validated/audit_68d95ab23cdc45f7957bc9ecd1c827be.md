### Title
Missing sender-binding on `savedCallbacks[MessageId]` allows cross-user response hijacking in the legacy WebAPI gateway handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Mantle report describes a class of bug where a piece of on-chain state needed to route a reward/response contains no information binding it to the original submitter, so it can be copied/replayed by a third party who then races to claim what was meant for the original sender. The chainlink analog is in the internet-facing Gateway's legacy WebAPI trigger handler: the request↔response correlation key (`MessageId`) is a client-chosen, unauthenticated string with no per-sender uniqueness enforcement, and the map that stores the pending callback is overwritten by whichever request arrives last with that ID — allowing one unprivileged client to hijack the response meant for another client's in-flight request.

### Finding Description
In `HandleLegacyUserMessage`, the handler stores the caller's callback keyed purely by the client-supplied `MessageId`, with no existence check and no binding to the requester's identity: [1](#0-0) 

`MessageId` validation only checks length bounds and a trailing-null-byte rule — it is never required to be unique, unpredictable, or bound to a signer: [2](#0-1) 

The lookup path (`HandleNodeMessage` → `handleWebAPITriggerMessage`) verifies that the *node* address matches the message sender (an anti-spoofing check for DON members), but performs no check that the original *user* who owns the `savedCallbacks[MessageId]` entry is the same user for whom the DON is now responding: [3](#0-2) [4](#0-3) 

Because the map assignment (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) has no guard against an existing key, an attacker who submits a second legacy request using the same `MessageId` as a victim's still-pending request will silently overwrite the victim's callback entry with their own. When the DON later returns the response echoing that `MessageId` (as it must, since `ValidatedRequestFromMessage`/`ValidatedResponseFromMessage` round-trip the ID unchanged), the gateway delivers the response to whichever callback is currently registered under that ID — the attacker's, not the victim's.

This is directly analogous to the reported bug: the routing/reward key (`MessageId` in chainlink; the slashing-report payload in Mantle) carries no sender-unique binding, so a second party can insert themselves into the flow and receive what the honest original party was owed.

Notably, other gateway handlers in the same codebase already apply the correct mitigation pattern that this handler is missing:
- The v2 HTTP trigger handler rejects duplicate/in-flight IDs outright: [5](#0-4) 
- The vault handler likewise rejects a request ID that already has an active entry: [6](#0-5) 

The legacy `capabilities` handler (and the near-identical `dummyHandler`, which has the same unguarded write pattern) lacks this check: [7](#0-6) 

### Impact Explanation
An unprivileged client of the public Gateway HTTP endpoint can intercept the response to another user's legacy WebAPI trigger request simply by guessing or observing the victim's `MessageId` (client-chosen, sent in plaintext over the request) and quickly submitting a colliding request. This is a cross-user response confusion: the victim's client will hang/timeout waiting for a response that is instead delivered to the attacker, and the attacker receives a response object addressed to the victim's request context. Depending on what data the WebAPI trigger payload carries back to the caller, this could leak information intended for another user or disrupt (deny) delivery of a legitimate response.

### Likelihood Explanation
Exploitation requires only that the attacker send a second, unauthenticated legacy gateway request with the same `MessageId` while the victim's request is still pending in `savedCallbacks` (default TTL up to `defaultCallbackMaxAgeSec` = 120s). No authentication bypass or node compromise is needed — `MessageId` values are visible to whoever crafts a request and are not scoped to a session or requester identity, so if `MessageId`s are ever predictable, sequential, or observable (e.g., via client-side scripts, logs, or shared IDs from a caller's own tooling), collision is straightforward for an unprivileged client.

### Recommendation
Add the same guard used elsewhere in the gateway: reject the request (or return a conflict error) in `HandleLegacyUserMessage` if `savedCallbacks[msg.Body.MessageId]` already exists, mirroring `httpTriggerHandler.setupCallback`'s `ErrConflict` behavior and `vault` handler's `newActiveRequest` existence check. Additionally, consider deriving/validating uniqueness of `MessageId` per authenticated sender (e.g., `sender+MessageId` as the map key) so that even without a collision-rejection error, one client cannot influence the routing key for another client's request.

### Proof of Concept
1. Client A submits a legacy WebAPI trigger request with `body.message_id = "X"` to the gateway; the gateway stores A's callback under `savedCallbacks["X"]` and forwards it to the DON (`HandleLegacyUserMessage`, lines 411-414 above).
2. Before the DON responds, Client B submits its own legacy request also using `body.message_id = "X"` (nothing prevents this — validation only checks length/null bytes, see `message.go` lines 61-66). This overwrites `savedCallbacks["X"]` with B's callback.
3. The DON responds to A's original message, echoing `MessageId = "X"` (message IDs round-trip via `ValidatedRequestFromMessage`/`ValidatedResponseFromMessage`).
4. `HandleNodeMessage` → `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds B's callback, and delivers the DON's response (intended for A) to B instead.

Note: I was not able to execute this scenario in a live environment (no test harness available in this session); the described flow is derived directly from reading the cited handler code, which shows no de-duplication or sender-binding on the `savedCallbacks` map versus the guarded implementations found elsewhere in the same package family.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
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

**File:** core/services/gateway/handlers/vault/handler.go (L466-472)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-82)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
	params, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	for _, member := range d.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
}
```
