### Title
Cross-user response misdelivery due to `savedCallbacks` keyed only by attacker-controlled `MessageId` with no sender scoping - ([File: core/services/gateway/handlers/handler.dummy.go])

### Summary
`dummyHandler` stores pending user callbacks in `d.savedCallbacks` keyed solely by `msg.Body.MessageId`, a value fully chosen by the requesting client and never required to be unique across senders. Two distinct, unrelated unauthenticated/self-signed clients can submit legacy requests with the same `MessageId`, and the second request's callback silently overwrites the first's, causing the first user's node response to be delivered to the second user (or lost entirely).

### Finding Description
`HandleLegacyUserMessage` unconditionally stores the callback keyed only by the message ID: [1](#0-0) 
`MessageId` originates entirely from the client-supplied `api.Message.Body.MessageId` field and is only checked for length/null-suffix, not uniqueness or binding to the signer: [2](#0-1) 
Any two distinct callers, each authenticating only with their own valid ECDSA signature (`key1`, `key2` — unprivileged, unauthenticated-to-the-node attackers from the gateway's perspective), can pick an identical `MessageId` string such as `"dup"`. Both requests reach `gateway.ProcessRequest`, are `Validate()`-d (which only extracts and sets `Sender`, not uniqueness), and are routed to `d.HandleLegacyUserMessage`: [3](#0-2) 
If request B (key2) arrives before request A's (key1) response completes, `d.savedCallbacks["dup"]` is overwritten with B's callback, discarding the reference to A's callback with no error or signal.

When a DON member's response arrives, `HandleNodeMessage` re-derives the lookup key purely from the response's own ID/Sender fields, independent of which original request it was meant to answer: [4](#0-3) 
The `if nodeAddr != msg.Body.Sender` check only verifies that the node connection which delivered this particular response is consistent with the response's own signature — it authenticates node-to-message binding, not which user's outstanding request the response should satisfy. It does nothing to prevent the map collision, since the lookup key (`MessageId`) was already corrupted by the second request's overwrite. Consequently, whichever response first arrives under ID `"dup"` gets delivered to whatever callback currently occupies that map slot — which may be user B's callback even though the response corresponds to user A's request — and the map entry is deleted, so the true owner (A) never receives any response and times out.

### Impact Explanation
This is a cross-user response confusion / broken request isolation vulnerability: one user's request data (echoed in the node response) can be delivered to a different, unrelated user's callback, and the legitimate requester is denied delivery of their own response (effectively a per-request denial of service triggered by another unprivileged user). This matches the "cross-user response redirection / denial of legitimate user's response delivery" bounty impact class for gateway request/response isolation.

### Likelihood Explanation
Any unauthenticated/self-signed client of the gateway HTTP API can trigger this: it only requires generating two legacy signed messages with the same `MessageId` value and submitting them close enough in time that the second overwrites the first's entry in `d.savedCallbacks` before the first's response arrives. `MessageId` uniqueness is entirely client-controlled and unenforced, and `DummyHandlerType` ("dummy") is a supported, first-class handler type registered in `handlerFactory.NewHandler`, not test-only scaffolding: [5](#0-4) 
The race window is realistic given the multi-node fan-out and network round trip to DON members before a response returns.

### Recommendation
Scope `savedCallbacks` keys by both `MessageId` and the requesting `Sender` (e.g., composite key `sender+":"+messageId`), or reject/queue a new request when a `MessageId` collision is detected for a still-pending different sender, instead of silently overwriting the existing callback entry.

### Proof of Concept
Extend `handler.dummy_test.go` with a test that:
1. Creates two `api.Message`s with the same `Body.MessageId` ("dup") but signed by two different private keys (`key1`, `key2`), producing distinct `Body.Sender` values after `Validate()`.
2. Calls `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` then, before resolving, calls `handler.HandleLegacyUserMessage(ctx, msgB, cbB)`.
3. Asserts `len(handler.savedCallbacks) == 1` and that the stored callback is `cbB`, not `cbA`.
4. Simulates a node response for msgA's content/ID and Sender-consistent nodeAddr via `handler.HandleNodeMessage(ctx, respA, msgA.Body.Sender)`.
5. Asserts that `cbB.Wait(ctx)` (not `cbA.Wait(ctx)`) receives the response intended for A, while `cbA.Wait(ctx)` times out/never resolves — demonstrating cross-user response misdelivery and denial of delivery to the rightful sender.

### Citations

**File:** core/services/gateway/handlers/handler.dummy.go (L62-66)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
```

**File:** core/services/gateway/handlers/handler.dummy.go (L90-107)
```go
	msg.Body.MessageId = resp.ID
	err = msg.Validate()
	if err != nil {
		return err
	}
	if nodeAddr != msg.Body.Sender {
		return fmt.Errorf("node address %s does not match message sender %s", nodeAddr, msg.Body.Sender)
	}
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

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
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

**File:** core/services/gateway/handler_factory.go (L78-80)
```go
	switch handlerType {
	case DummyHandlerType:
		return handlers.NewDummyHandler(donConfig, don, hf.lggr)
```
