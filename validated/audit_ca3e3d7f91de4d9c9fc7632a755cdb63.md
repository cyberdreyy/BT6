This confirms the vulnerability is real and notably inconsistent with the design used elsewhere in the same package. The `RequestCache` used by other handlers (e.g. `confidentialrelay`) keys pending requests by `globalId{sender, id}` [1](#0-0)  and explicitly rejects collisions with `"request already exists"` [2](#0-1) , but the capabilities `handler` bypasses this safe pattern entirely and uses a raw `map[string]*savedCallback` keyed only by `msg.Body.MessageId` with an unconditional overwrite.

### Title
Cross-user response hijacking via `savedCallbacks` map keyed only by attacker-chosen `MessageId` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each pending user callback in `h.savedCallbacks[msg.Body.MessageId]` without checking for an existing entry or scoping the key to the sender, unlike the sibling `RequestCache` implementation which keys on `{sender, id}` and rejects duplicates. Because `MessageId` is fully attacker-chosen and only length/suffix-validated in `api.Message.Validate()`, two independent unauthenticated users submitting requests with a colliding `MessageId` to the same DON can cause user B's callback to overwrite user A's, so the eventual DON node response for A's trigger is delivered to B's HTTP connection instead of A's.

### Finding Description
The gateway's HTTP entrypoint `gateway.ProcessRequest` validates the incoming `api.Message` only via `msg.Validate()`, which checks signature format, length limits, and null-byte suffixes on `MessageId`, `Method`, and `DonId`, but never enforces per-sender uniqueness of `MessageId` [3](#0-2) . It then routes the message to the target DON handler via `h.HandleLegacyUserMessage(ctx, msg, callback)` [4](#0-3) .

In `handler.HandleLegacyUserMessage`, after validating the payload/timestamp/method, the handler stores the pending callback like this:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [5](#0-4) 

This is an unconditional map write — no check for an existing key, and the key is `msg.Body.MessageId` alone, not scoped by `msg.Body.Sender`. When a DON node later responds, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId`:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
if found {
    return savedCb.SendResponse(...)
}
``` [6](#0-5) 

If attacker A submits a trigger request with `MessageId = X`, and shortly after, a second requester (B, could be another unprivileged client or an attacker deliberately guessing/colliding a known/predictable ID) submits a different trigger request with the same `MessageId = X` to the same DON, B's entry overwrites A's in `savedCallbacks[X]`. Both requests are forwarded to all DON members via `don.SendToNode`. When any DON member responds to A's original trigger (echoing `MessageId = X`), `handleWebAPITriggerMessage` finds the entry currently in the map — which is now B's callback — deletes it, and delivers A's response payload to B's HTTP connection. A's connection instead receives nothing (until any subsequent request-scoped timeout at the gateway layer), causing both response leakage to B and denial of the legitimate response to A.

This directly contrasts with the codebase's own established safe pattern in `common.RequestCache`, which scopes keys by `globalId{sender, id}` [1](#0-0)  and rejects duplicate registrations with an explicit error [2](#0-1) , proving the maintainers are aware collision protection is required for this exact class of pending-request map but omitted it in the `capabilities` handler.

### Impact Explanation
This is cross-user response confusion: an unprivileged user (or attacker) can receive another user's trigger response payload delivered over their own HTTP connection to the gateway, and can simultaneously deny the legitimate requester their response. Depending on the workflow/trigger payload content forwarded back through `Response`/`sendHTTPMessageToClient`, this could leak data intended for another user. This matches the bounty's "cross-user response confusion" impact class.

### Likelihood Explanation
No privilege is required beyond being able to send a signed `api.Message` to the gateway's legacy HTTP endpoint (any external caller with a valid ECDSA keypair, since `Validate()` only checks structural correctness, not sender identity against `MessageId`). The only precondition is that both requests use the same `MessageId` and target the same `DonId` within the callback TTL window (`defaultCallbackMaxAgeSec` = 120s) [7](#0-6) . If two attacker-controlled clients are used, this is 100% reproducible; against a real victim it depends on the victim's client using a predictable/enumerable `MessageId` scheme, which is plausible for many simple client SDKs (e.g., counters, timestamps) but not guaranteed for random UUIDs.

### Recommendation
Scope `savedCallbacks` keys by `{Sender, MessageId}` (mirroring `common.RequestCache`'s `globalId`) and reject/reuse-detect duplicate registrations for the same key with an explicit error instead of silently overwriting, e.g. check `if _, exists := h.savedCallbacks[key]; exists { return error }` before inserting in `HandleLegacyUserMessage`, and use the same composite key in `handleWebAPITriggerMessage`'s lookup/delete. Ideally, migrate this handler to reuse `common.RequestCache` directly rather than maintaining a parallel, less-safe map.

### Proof of Concept
Add a handler-level Go test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build two distinct signed `api.Message`s (different private keys / senders) both using the same `MessageId` (e.g., `"12345"`) and same `DonId`, using the existing `triggerRequest` helper but forcing identical `MessageId`.
2. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` then `handler.HandleLegacyUserMessage(ctx, msgB, cbB)`.
3. Assert `handler.savedCallbacks["12345"]` now references cbB's callback (overwrite confirmed).
4. Simulate a DON node response for msgA (echoing `MessageId = "12345"`) via `handler.HandleNodeMessage`.
5. Assert `cbB.Wait(ctx)` returns msgA's response payload (cross-delivery), while `cbA.Wait(ctx)` times out/never resolves — demonstrating that user A's response was misdelivered to user B.

### Citations

**File:** core/services/gateway/handlers/common/requestcache.go (L34-37)
```go
type globalId struct {
	sender string
	id     string
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L60-63)
```go
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```

**File:** core/services/gateway/api/message.go (L54-87)
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
```

**File:** core/services/gateway/gateway.go (L267-269)
```go
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-43)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
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
