### Title
Cross-User Response Hijacking via Attacker-Controlled `MessageId` Collision in Gateway WebAPI Capabilities Handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The bug class described in the external report — a predictable, unprotected state-transition identifier being exploited by an unprivileged actor to intercept value intended for another party — has a direct analog in the Chainlink Gateway's `capabilities` handler. The `MessageId` used to key the `savedCallbacks` map that routes DON responses back to HTTP clients is taken verbatim from the attacker-controlled JSON-RPC request `ID` field, with no binding to session, client identity, or connection. This allows an unprivileged external client to race or collide `MessageId` values and receive a response that belongs to a different requester.

### Finding Description
When an external, unauthenticated HTTP client submits a legacy Gateway request, `ProcessRequest` decodes it and calls `HandleLegacyUserMessage`, which stores the caller's callback keyed purely by the client-supplied message ID: [1](#0-0) [2](#0-1) 

The only validation performed on this ID is a length/null-byte check in `Message.Validate()` — there is no uniqueness enforcement, no binding to the requesting connection, and no randomness requirement: [3](#0-2) 

Later, when a DON node responds with `MethodWebAPITrigger`, the handler looks up `savedCallbacks` by that same ID and delivers the response to whichever callback is currently stored there, deleting the entry immediately: [4](#0-3) 

Because two different unprivileged clients can independently choose (or brute-force/predict) the same `MessageId` value, an attacker who submits a request with an ID matching (or racing) a victim's in-flight request can overwrite the victim's saved callback in the shared map. When the DON's response for the victim's original request arrives, it is delivered to whichever callback is currently registered under that ID — potentially the attacker's — while the true owner either times out or (depending on race order) receives the attacker's response instead. This is structurally the same root cause as the sandwiched-yield report: a predictable, externally influenceable update key with no protection against a party racing/timing around it to intercept a benefit (here, a response payload) meant for someone else.

### Impact Explanation
This is a cross-user response confusion vulnerability: an unprivileged HTTP client interacting with the internet-facing gateway can cause its callback to be invoked with data intended for another user's request (or vice versa), depending on race timing. Depending on what data flows through `MethodWebAPITarget`/`MethodComputeAction`/`MethodWorkflowSyncer`/`MethodWebAPITrigger` payloads, this can leak response content across trust boundaries or let an attacker inject a response into another party's outstanding request.

### Likelihood Explanation
Exploitability requires only unauthenticated HTTP access to the gateway's request endpoint and the ability to guess or observe a victim's `MessageId` (e.g., via short/predictable ID schemes used by some legacy DON job configs) and race the timing window between callback registration and DON response delivery. No privileged role or node compromise is required, satisfying the "unprivileged-actor analog" constraint.

### Recommendation
- Do not use a fully client-controlled ID as the sole map key for callback routing. Generate the `savedCallbacks` key server-side (e.g., UUID) and bind it internally to the client-supplied ID for correlation only in the response payload.
- Enforce uniqueness checks against currently outstanding `MessageId`s and reject/queue collisions instead of overwriting.
- Bind callback registration to the originating connection/session so a response can only be delivered to the same logical request context that created it.

### Proof of Concept
1. Victim (unprivileged client) submits a legacy Gateway request via HTTP with `id: "abc123"` for `MethodWebAPITrigger`, which stores its callback in `savedCallbacks["abc123"]` ( [2](#0-1) ).
2. Before the DON responds, an attacker submits its own request also using `id: "abc123"`, overwriting `savedCallbacks["abc123"]` with the attacker's callback.
3. When the DON later responds for the victim's original request (correlated only by `MessageId`), `handleWebAPITriggerMessage` looks up `savedCallbacks["abc123"]`, finds the attacker's callback, and delivers the victim's response to the attacker ( [4](#0-3) ).
4. The victim's original callback is discarded/never invoked, and the attacker receives a response payload it did not request.

### Citations

**File:** core/services/gateway/handlers/common/message_util.go (L46-57)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
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

**File:** core/services/gateway/api/message.go (L54-67)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
```
