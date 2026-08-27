### Title
Cross-user response confusion via unscoped `MessageId` key collision in `dummyHandler.savedCallbacks` - ([File: core/services/gateway/handlers/handler.dummy.go])

### Summary
`dummyHandler.HandleLegacyUserMessage` stores each user's `Callback` in `d.savedCallbacks` keyed solely by the attacker-supplied `msg.Body.MessageId`, with no per-sender namespacing and no duplicate-ID rejection. Two different signed senders using the same `MessageId` will overwrite each other's map entry, and `HandleNodeMessage` will deliver the DON's response to whichever `Callback` is currently stored under that key, not necessarily the requester who actually sent that particular request.

### Finding Description
`HandleLegacyUserMessage` unconditionally does: [1](#0-0) 
storing `&savedCallback{msg.Body.MessageId, callback}` under `d.savedCallbacks[msg.Body.MessageId]` with no check for an existing entry. `MessageId` is fully attacker-controlled (any signed sender can set `Body.MessageId` to any string ≤128 bytes, per `Message.Validate`): [2](#0-1) 

`HandleNodeMessage` later looks up and deletes the entry purely by `MessageId`, and sends the DON's response to whatever `Callback` is found: [3](#0-2) 

There is no per-sender scoping of the map key (e.g., `sender+messageId`) and no rejection of a duplicate/in-flight `MessageId`. This is in contrast to another handler in the same package family, `confidentialrelay`, which explicitly rejects a request whose ID is already in flight ("request ID already exists"), confirming that the missing uniqueness check in `dummyHandler` is a deviation from the established pattern used elsewhere in the gateway handlers. [4](#0-3) 

Exploit flow: attacker A sends `HandleLegacyUserMessage` with `MessageId = X`; concurrently (or shortly after) a different, unrelated user B also sends a request with the same `MessageId = X` (nothing prevents an attacker from guessing/colliding, or the collision can even happen accidentally between two legitimate distinct users if a client reuses IDs). B's write overwrites A's callback entry in `d.savedCallbacks["X"]`. When the DON's response to A's original request under ID X arrives at `HandleNodeMessage`, it is delivered to B's `Callback.SendResponse`, i.e., B receives (and A never receives) the response payload for request X. If the roles are reversed timing-wise, A could instead receive B's response.

### Impact Explanation
This is a cross-user response confusion: response content intended for one user's request is delivered to a different, unrelated user's callback channel. Depending on what the dummy/DON handler payload contains, this could leak data or trigger a workflow-side-effect confusion (a user receiving the outcome of a request they never issued, or losing their own legitimate response). This matches the "cross-user response confusion" impact class explicitly called out in the audit scope.

### Likelihood Explanation
The precondition is minimal: only the ability to send any signed gateway message with an arbitrary `MessageId` (any authenticated gateway user has this, since `MessageId` is client-chosen and only length/format constrained, not uniqueness constrained). Multiple truly independent senders (not just an attacker) could plausibly collide on request IDs (e.g., both clients use simple counters starting at "1", or a poorly-seeded UUID/timestamp scheme), which increases realistic likelihood beyond a targeted attack. Two requests need to be in flight concurrently (i.e., before the corresponding DON response arrives) for the race to manifest, which is a narrow but reproducible timing window given that `HandleLegacyUserMessage` sends the request to nodes and then waits, in a way not synchronized to a per-ID lock lifetime.

### Recommendation
- Reject a `HandleLegacyUserMessage` call if `d.savedCallbacks[msg.Body.MessageId]` already exists (mirroring the pattern used in the `confidentialrelay` handler that returns "request ID already exists").
- Alternatively/additionally, scope the map key by `(sender, MessageId)` rather than `MessageId` alone, so that collisions across different senders cannot occur, and validate on `HandleNodeMessage` that the returned message's sender matches the sender that originally submitted the stored callback.

### Proof of Concept
Go test plan (extends `handler.dummy_test.go`):
1. Construct two distinct signed `api.Message`s from two different private keys/senders, both using `Body.MessageId = "collide"`, `DonId`/`Method` set appropriately.
2. Call `handler.HandleLegacyUserMessage(ctx, &msgA, cbA)` then, before delivering any node response, call `handler.HandleLegacyUserMessage(ctx, &msgB, cbB)` — assert both succeed with no duplicate-ID error (demonstrating the missing check) and that `d.savedCallbacks["collide"]` now only contains B's callback (can be verified indirectly through step 3).
3. Call `handler.HandleNodeMessage(ctx, respForA, msgA.Body.Sender)` where `respForA` is a validly signed/matching node response addressed to ID `"collide"` from a node that was actually responding to A's request.
4. Assert that `cbB.Wait(ctx)` receives the response (unexpectedly, since it belongs to A) while `cbA.Wait(ctx)` times out / never resolves — proving cross-user delivery of the response intended for A into B's callback channel.

### Citations

**File:** core/services/gateway/handlers/handler.dummy.go (L62-66)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
```

**File:** core/services/gateway/handlers/handler.dummy.go (L98-107)
```go
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(&msg), ErrorCode: api.NoError})
	}
```

**File:** core/services/gateway/api/message.go (L54-66)
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
