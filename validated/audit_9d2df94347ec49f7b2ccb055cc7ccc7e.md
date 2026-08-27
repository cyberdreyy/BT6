### Title
Cross-user response confusion via MessageId-only keyed savedCallbacks map in gateway capabilities handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`gateway.ProcessRequest` creates a fresh `handlerscommon.Callback` per request and hands it to `handler.HandleLegacyUserMessage`, which stores it in `h.savedCallbacks` keyed **only** by `msg.Body.MessageId`, with no sender/requester binding. Because `MessageId` is fully attacker-controlled (client-chosen, up to 128 bytes, only null-suffix restricted), a second request using the same `MessageId` as a victim's still-pending request will overwrite the map entry, causing the node's eventual response (delivered by messageId lookup only) to be routed to whichever caller's callback is currently registered — potentially the attacker's — instead of the original requester's.

### Finding Description
`gateway.ProcessRequest` at [1](#0-0)  creates `callback := handlerscommon.NewCallback()` per call and invokes `h.HandleLegacyUserMessage(ctx, msg, callback)`, then blocks on `callback.Wait(ctx)`.

In the capabilities handler, `HandleLegacyUserMessage` stores this callback in a shared map keyed solely by `msg.Body.MessageId`: [2](#0-1) 

When a node later responds, the response is matched back to a caller purely by `msg.Body.MessageId`, with no check that the request originally associated with that ID belongs to the same sender: [3](#0-2) 

`MessageId` uniqueness is not enforced by `Message.Validate()` beyond length/null-suffix checks — it does not tie the ID to the sender or reject duplicates: [4](#0-3) 

Exploit flow:
1. Victim submits a signed legacy request (`web_api_trigger`/webAPI target flow) with `MessageId = X`. `HandleLegacyUserMessage` stores victim's callback at `h.savedCallbacks["X"]` and forwards the request to DON nodes; the victim's goroutine blocks in `callback.Wait(ctx)`.
2. Before the DON responds, an attacker submits their own signed request (any valid signature from their own key, since `Validate()`/`ExtractSigner` only checks the signature corresponds to *some* address, not that it matches a specific expected sender) with the *same* `MessageId = X`. This call again reaches `HandleLegacyUserMessage`, which does `h.savedCallbacks["X"] = &savedCallback{...attacker's callback...}`, overwriting the victim's entry in the map.
3. When a node response tagged with `MessageId = X` arrives (whether it corresponds to the trigger fired for the victim's original request or the attacker's), `handleWebAPITriggerMessage` looks up `h.savedCallbacks[msg.Body.MessageId]` — now the attacker's callback — deletes the entry, and calls `savedCb.SendResponse(...)`, delivering that payload to the attacker instead of the victim.
4. The victim's original callback reference has been evicted from the map; their `callback.Wait(ctx)` in `gateway.ProcessRequest` will time out (`api.RequestTimeoutError`), while the attacker receives the victim's response payload.

This exact same MessageId-only keying pattern also exists in the dummy handler (`h.savedCallbacks[msg.Body.MessageId]`), confirming the pattern is structural rather than a one-off bug: [5](#0-4)  and node-response lookup at [6](#0-5) .

No existing check in `gateway.ProcessRequest`, `Message.Validate()`, or the capabilities handler enforces per-sender uniqueness of `MessageId`, nor does the response-delivery path verify that the responding node's message sender/receiver matches the original requester before handing the payload back through the saved callback.

### Impact Explanation
An unprivileged attacker who can guess or observe a victim's in-flight `MessageId` (e.g., via timing correlation, shared client library defaults, or predictable ID generation) can cause the gateway to deliver the victim's capability response (e.g., web API trigger payload data) to the attacker instead of the victim — a cross-user response confusion / data disclosure to an unauthorized party, and simultaneously a denial-of-service (timeout) for the victim's request. This matches the "unauthorized action on another user's job/subscription/secret" / "attacker-controlled data returned to another user" impact class.

### Likelihood Explanation
Exploitability requires: (1) the attacker can send a validly signed legacy gateway request (any registered signer key qualifies — no special privilege needed beyond DON allowlist membership if one exists at a separate layer), and (2) the attacker knows or can guess the victim's pending `MessageId` before it resolves. Since `MessageId` is entirely client-chosen and its only restriction is length (≤128 bytes) and no trailing null byte, and there is no requirement for global or per-sender uniqueness, an attacker able to observe or predict identifiers used by common client SDKs, or one racing against their own triggered scenario, can reliably reproduce the race. The race window is bounded by the time it takes the DON to process and respond, which is realistically nontrivial (network round trips to nodes), giving a feasible window for a concurrent submission.

### Recommendation
Key `savedCallbacks` (and any similar per-request tracking maps) by a composite key that binds both the requester's identity and the `MessageId`, e.g. `(sender, messageId)` or `(donId, sender, messageId)`, rather than `messageId` alone. When storing a new callback, reject/overwrite-protect against an existing unexpired entry, and validate that node responses match the recorded sender/receiver before dispatching to the saved callback. Apply the same fix to `handler.dummy.go` and any other handler using this per-messageId map pattern.

### Proof of Concept
Go handler-level integration test plan (in `core/services/gateway/handlers/capabilities` or via `gateway_test.go` with the capabilities handler wired in):
1. Set up a `handler` (capabilities) with a mocked `DON` that records `SendToNode` calls but does not immediately respond.
2. Victim: call `HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` where `victimMsg.Body.MessageId = "dup-id"`, signed by victim's key. Do not resolve any node response yet.
3. Attacker: call `HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` where `attackerMsg.Body.MessageId = "dup-id"` (same ID), signed by attacker's key, before any node response for the victim arrives.
4. Simulate one node responding via `handleWebAPITriggerMessage`/`HandleNodeMessage` with `MessageId = "dup-id"` and a payload that should belong to the victim's trigger.
5. Assert that `attackerCallback` receives the response payload intended for the victim (`attackerCallback`'s underlying channel receives the victim-originated payload), and that `victimCallback.Wait(ctx)` times out or never resolves — demonstrating the overwrite and cross-user leak.
6. As a regression test for the fix, after keying by `(sender, messageId)`, repeat the same steps and assert each of `victimCallback` and `attackerCallback` independently receives only their own response, with no interference.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L54-88)
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
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-66)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
```

**File:** core/services/gateway/handlers/handler.dummy.go (L98-101)
```go
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()
```
