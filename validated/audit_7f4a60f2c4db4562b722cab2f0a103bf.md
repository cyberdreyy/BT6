Confirmed: `MessageId` is fully attacker-controlled (client-supplied field in `MessageBody`, only length/format validated in `Message.Validate()`), and it is **not** bound to the sender/signer identity. [1](#0-0) [2](#0-1) 

In `HandleLegacyUserMessage`, the callback is stored unconditionally by simple map assignment with no existence check:

```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [3](#0-2) 

If two requests race with the same `MessageId`, the second `HandleLegacyUserMessage` call overwrites `h.savedCallbacks[X]`, losing the reference to the first (victim's) callback. Then when the DON responds via `handleWebAPITriggerMessage`, it looks up and deletes the single entry keyed by `MessageId` and delivers the response to whichever callback is currently stored: [4](#0-3) 

### Title
Legacy gateway callback map keyed only by attacker-controlled MessageId allows cross-user response hijacking - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` stores the per-request HTTP callback in `h.savedCallbacks` keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to the sender/signer and no collision check. An unauthenticated (but signed) attacker who submits a legacy request with the same `MessageId` as a victim's in-flight request overwrites the victim's saved callback, so the eventual DON response for that `MessageId` is delivered to the attacker's HTTP connection instead of the victim's.

### Finding Description
The gateway's `ProcessRequest` decodes an incoming JSON-RPC request, and for legacy DON-scoped requests calls `msg.Validate()` — which only checks length/format of `MessageId`, `Method`, `DonId`, and extracts `Sender` from the signature; it never checks `MessageId` for cross-sender uniqueness. [5](#0-4) [2](#0-1) 

`ProcessRequest` then calls `h.HandleLegacyUserMessage(ctx, msg, callback)` with a fresh `callback` per HTTP request. [6](#0-5) 

Inside `HandleLegacyUserMessage`, after validating the payload/method, the handler stores the callback keyed only by `msg.Body.MessageId`:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
```
There is no check for `found` / pre-existing entry before overwrite, and no composite key incorporating `msg.Body.Sender`. [3](#0-2) 

When the DON eventually responds, `handleWebAPITriggerMessage` looks up by `MessageId` alone and forwards the response to whatever callback currently occupies that slot, then deletes it:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
``` [4](#0-3) 

Exploit flow:
1. Victim POSTs a legacy request with `MessageId = "X"` (properly signed by victim's key). Gateway calls `HandleLegacyUserMessage`, which stores victim's callback at `savedCallbacks["X"]` and forwards the request to all DON members.
2. Before the DON responds, attacker (any party capable of producing a validly-signed message — signature validity does not require any privilege, just an ECDSA keypair) POSTs a second legacy request also with `MessageId = "X"` but a different `Sender`. Since `Validate()` and `HandleLegacyUserMessage` never check for collision against sender identity, this succeeds and overwrites `savedCallbacks["X"]` with the attacker's callback.
3. Victim's original `handlers.Callback` reference is discarded (garbage, unreferenced), and it will eventually time out.
4. When the DON responds for `MessageId = "X"` (the response is keyed only by MessageId, the DON does not know which HTTP client waits), `handleWebAPITriggerMessage` delivers the response to `savedCallbacks["X"]`, which is now the attacker's callback — attacker receives the victim's response over their own HTTP connection.

Because the request forwarded to the DON also carries `msg.Body.Sender` (attacker's), the DON nodes process the attacker's own triggered request and the attacker also gets a legitimate response to their own request, but this doesn't stop the described race — both requests are forwarded to the DON with `MessageId="X"`; the DON returns using this same `MessageId`, and both DON responses (if the DON processes both legacy triggers) go to the single map slot, whichever callback is currently registered wins, and `found` logic doesn't handle two outstanding requests per ID at all — the second write clobbers the first regardless of ordering.

### Impact Explanation
This breaks the ISOLATION invariant between independent gateway user requests: an unprivileged attacker who can send a signed gateway request can redirect another user's DON-computed workflow response (which may contain data results, secrets returned via legacy web_api_trigger flows, or execution status) to themselves. This matches the "cross-user response confusion" impact class — unauthorized disclosure of another user's request/response data via request/response mismatch, without any authentication bypass on the attacker's own request (attacker still needs to submit a validly signed message, but does not need to know the victim's key or the victim's response — only guess/collide on `MessageId`).

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or brute-force a currently in-flight victim `MessageId` and win the race before the DON responds. If `MessageId` is client-chosen (there is no server-side enforcement of randomness/uniqueness — `Validate()` only checks length/format), an attacker who can observe or guess a victim's `MessageId` (e.g., if client apps use predictable IDs such as incrementing counters, timestamps, or fixed values) can trivially win this race by sending their own request with the same ID. Given `MaxAllowedMessageAgeSec`/`CallbackMaxAgeSec` windows (~120s default) [7](#0-6) , an attacker has a window on the order of the DON round-trip latency to win the race, which is realistic for automated flooding of guessed/observed IDs. The credential bar is low: any party able to construct one ECDSA-signed gateway message (no allowlist enforced at this stage per the `// TODO: apply allowlist and rate-limiting here` comment) [8](#0-7) .

### Recommendation
Bind the saved-callback key to both `MessageId` and `Sender` (e.g. `fmt.Sprintf("%s:%s", msg.Body.Sender, msg.Body.MessageId)`), and/or reject a new legacy request outright if an unexpired callback already exists for the same `MessageId` (return a collision error to the second caller instead of silently overwriting). Additionally consider server-side generation or uniqueness enforcement of `MessageId` scoped per sender.

### Proof of Concept
Go unit test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` with a mock `handlers.DON` that captures forwarded requests but does not immediately reply.
2. Build `msgVictim` with `MessageId = "X"`, signed by victim key (`Sign`), and `msgAttacker` with the same `MessageId = "X"`, signed by a different attacker key.
3. Call `h.HandleLegacyUserMessage(ctx, msgVictim, victimCallback)`, then call `h.HandleLegacyUserMessage(ctx, msgAttacker, attackerCallback)` before any DON response arrives.
4. Assert `h.savedCallbacks["X"]` now equals `attackerCallback`'s wrapped `savedCallback`, not `victimCallback`'s (demonstrating overwrite).
5. Simulate DON response for `MessageId="X"` via `handleWebAPITriggerMessage`; assert the response is delivered to `attackerCallback.Wait()` and `victimCallback.Wait()` times out/never resolves — proving cross-user response delivery.
6. Expected (fixed) behavior: the second `HandleLegacyUserMessage` call should either be rejected (distinct key per sender) or return an error/collision response, and `victimCallback` should still correctly receive the DON response for `MessageId="X"` signed by the victim.

### Citations

**File:** core/services/gateway/api/message.go (L42-52)
```go
type MessageBody struct {
	MessageId string `json:"message_id"`
	Method    string `json:"method"`
	DonId     string `json:"don_id"`
	Receiver  string `json:"receiver"`
	// Service-specific payload, decoded inside the Handler.
	Payload json.RawMessage `json:"payload,omitempty"`

	// Fields only used locally for convenience. Not serialized.
	Sender string `json:"-"`
}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

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

**File:** core/services/gateway/gateway.go (L266-269)
```go
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```
