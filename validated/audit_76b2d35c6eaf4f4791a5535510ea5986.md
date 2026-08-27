### Title
Cross-user response hijack via unsigned/single-key `savedCallbacks` map keyed only by `MessageId` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each user's callback in `h.savedCallbacks[msg.Body.MessageId]` without checking whether an entry already exists for that key, and without binding the key to the requesting sender. `handleWebAPITriggerMessage` looks up and deletes by `MessageId` alone and forwards the first node response to whatever callback is currently stored there. An attacker who can predict or collide a victim's in-flight `MessageId` can overwrite the map entry and receive the victim's node response.

### Finding Description
The relevant map is defined and populated here: [1](#0-0) 

There is no existence check before the assignment — `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` unconditionally overwrites any prior entry for the same `MessageId`. Note the contrast with the newer `common.RequestCache.NewRequest`, which explicitly rejects duplicates: [2](#0-1) [3](#0-2) 

`requestCache` keys by `{sender, MessageId}` (`globalId`), i.e., binds ownership to the signer, and rejects duplicates. The legacy `handler.savedCallbacks` in `capabilities/handler.go` does neither — it keys purely by `msg.Body.MessageId`.

On the response side, `handleWebAPITriggerMessage` retrieves and deletes solely by `MessageId`, with no sender/requester binding check: [4](#0-3) 

**Reachability from an unprivileged/unauthenticated attacker:** the gateway's user-facing HTTP entrypoint `gateway.ProcessRequest` parses the JSON-RPC request into `msg`, calls `msg.Validate()` (signature format/length checks only, not ownership of MessageId), and dispatches to `h.HandleLegacyUserMessage(ctx, msg, callback)`: [5](#0-4) 

`msg.Validate()` only validates message shape and extracts the signer into `Sender` for later use, but `HandleLegacyUserMessage`/`handleWebAPITriggerMessage` never check `Sender` against the saved callback: [6](#0-5) 

`MessageId` is fully attacker-chosen (bounded to ≤128 bytes, no uniqueness/nonce enforcement) — see `Validate()`'s length check only: [7](#0-6) 

Because any signer can pick an arbitrary `MessageId` string, and the gateway does not tie `savedCallbacks` entries to sender identity, a second legacy trigger request from a different signer using the same `MessageId` as a victim's in-flight request silently overwrites `h.savedCallbacks[MessageId]`. When the DON node's first response for that `MessageId` arrives, `handleWebAPITriggerMessage` delivers it to whichever callback is currently stored — potentially the attacker's — because deletion/lookup is by `MessageId` only, not by sender or per-request nonce/UUID randomness enforced server-side.

### Impact Explanation
This is a cross-user response/callback confusion: the victim's HTTP client either hangs (eventually timing out) while the attacker's HTTP connection receives the victim's trigger-response payload. Depending on what data flows through `web_api_trigger` responses, this could disclose response content intended for another user (information disclosure) and/or confuse execution flow for the victim (denial of that request). This matches Chainlink's "cross-user information disclosure / response confusion" impact class for the gateway.

### Likelihood Explanation
Exploitability requires: (1) ability to submit arbitrary signed legacy JSON-RPC requests to the gateway's public user HTTP port — a capability available to any unprivileged/unauthenticated API client, since the gateway's `ProcessRequest` performs no per-user allowlisting for legacy requests beyond DON-ID lookup; and (2) predicting or colliding the victim's in-flight `MessageId` within its short 2-minute default callback TTL (`defaultCallbackMaxAgeSec`). If client-side `MessageId` generation is low-entropy, sequential, or otherwise guessable (this repo does not show a shared client-side generator enforcing randomness — it's caller-supplied), the race is straightforward to win by repeatedly submitting requests with candidate IDs. This is a realistic race-condition class bug, not purely theoretical, given the complete absence of a duplicate/ownership check that the newer `requestCache` implementation already added elsewhere in the same package tree.

### Recommendation
Bind `savedCallbacks` keys to the requesting sender, analogous to `requestCache`'s `globalId{sender, id}`, and reject inserts when an entry already exists for that key instead of silently overwriting it. On the response path, retrieve/delete using the same composite key and verify sender consistency before delivering the response, mirroring `common.RequestCache`'s existing `NewRequest`/`ProcessResponse` pattern. Ideally, migrate this handler to use `common.RequestCache` directly instead of maintaining its own duplicate `savedCallbacks` map.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct handler via `NewHandler` with default config.
2. Sign and send `msgVictim` (`MessageId = "M"`) via `HandleLegacyUserMessage` with `callbackVictim` (mock `handlers.Callback`), from signer A.
3. Before any node responds, sign and send `msgAttacker` (`MessageId = "M"`, same value) via `HandleLegacyUserMessage` with `callbackAttacker`, from a different signer B.
4. Assert no error/rejection occurred on step 3 (i.e., `HandleLegacyUserMessage` does not return an "already exists" error) — this demonstrates the silent overwrite.
5. Simulate a node response for `MessageId = "M"` by calling `HandleNodeMessage`/`handleWebAPITriggerMessage` with a `MethodWebAPITrigger` message.
6. Assert `callbackAttacker.SendResponse` was invoked with the response, and `callbackVictim.SendResponse` was never invoked — proving the victim's response was hijacked by the attacker's callback.

### Citations

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

**File:** core/services/gateway/handlers/common/requestcache.go (L34-37)
```go
type globalId struct {
	sender string
	id     string
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L57-63)
```go
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```

**File:** core/services/gateway/gateway.go (L250-269)
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

	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
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
