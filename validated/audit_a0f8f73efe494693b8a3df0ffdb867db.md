### Title
Cross-user response confusion via MessageId collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores the caller's callback keyed only by the attacker-controlled `msg.Body.MessageId`, with no uniqueness/collision check and no binding to the sender's identity, unlike the sibling `RequestCache` implementation used elsewhere which keys entries by `{sender, id}` and explicitly rejects duplicates. Two different unprivileged clients can pick the same `MessageId` for the same `DonId` and race their concurrent legacy requests, causing one callback registration to silently overwrite the other, leading to a lost response for one user and/or delivery of that response to the wrong user.

### Finding Description
The reachable path is: `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes a legacy request, validates the message structurally via `msg.Validate()` (`core/services/gateway/api/message.go:54-88`), resolves the handler by `msg.Body.DonId`, and calls `h.HandleLegacyUserMessage(ctx, msg, callback)` [1](#0-0) .

`Message.Validate()` only checks signature format, ID length/suffix, method/DonId length, and derives `Body.Sender` from the ECDSA signature — it performs no uniqueness check on `MessageId` across senders [2](#0-1) . `MessageId` is fully attacker-chosen (any string up to 128 bytes not ending in a null byte), so two distinct unprivileged clients (each signing with their own private key) can independently choose the identical `MessageId` string and target the same `DonId`.

In `capabilities/handler.go`, `HandleLegacyUserMessage` registers the callback with:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [3](#0-2) 
This is a plain map write keyed only by `MessageId` — there is no `sender` component in the key and no existence check (`if _, exists := ...; exists { return error }`) before overwrite, unlike the collision-checked `RequestCache.NewRequest`, which uses `key := globalId{request.Body.Sender, request.Body.MessageId}` and explicitly returns `"request already exists"` on collision [4](#0-3) .

When a DON node later responds, `HandleNodeMessage` validates only that the node's address matches `msg.Body.Sender` (i.e., that the response really comes from that node) and dispatches by method to `handleWebAPITriggerMessage`, which looks up and deletes the callback purely by `msg.Body.MessageId`:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
if found {
    return savedCb.SendResponse(...)
}
``` [5](#0-4) [6](#0-5) 
There is no verification that the response's originating client matches the client that registered the currently-stored callback for that `MessageId`.

Exploit flow: Client A submits a legacy `webAPITrigger` request with `DonId=X`, `MessageId=M` (signed by key A). Concurrently, Client B submits a legacy request with the same `DonId=X`, `MessageId=M` (signed by key B). Both requests are forwarded to all DON members (`don.SendToNode`) under their own signed message bodies. If B's `HandleLegacyUserMessage` map write executes after A's, `savedCallbacks[M]` now points to B's callback, silently discarding the reference to A's callback (the underlying Go channel referenced by A's callback goroutine is now orphaned). When a DON node's response for A's original trigger arrives (still carrying `MessageId=M`), `handleWebAPITriggerMessage` looks up `savedCallbacks[M]`, finds B's callback, and delivers A's response to B. A's own request either times out (`gateway.ProcessRequest`'s `callback.Wait(ctx)` deadline, per `core/services/gateway/gateway.go:278-285`) or, if the node responds to B's own forwarded copy of the message first, that response is delivered to A instead.

### Impact Explanation
This causes cross-user response confusion: an unprivileged, unauthenticated (mutually distrusting) client can capture a response payload intended for another client, and/or cause the other client's legitimate request to be silently dropped (denial of a single request, presented as `RequestTimeoutError`). Depending on what data the WebAPI trigger response contains (e.g., workflow execution results, event data), this constitutes unauthorized disclosure of another user's response data and disruption of their request — matching the "cross-user response confusion" impact class called out for this audit scope.

### Likelihood Explanation
No privileged credentials or roles are required — only the ability to sign an arbitrary message with a self-controlled ECDSA key and send it as a legacy gateway HTTP request with an attacker-chosen `MessageId` and a known/guessable `DonId`. The only precondition is a race: both requests' `HandleLegacyUserMessage` map writes for the same `DonId`+`MessageId` occurring before either response arrives. Since `MessageId` collisions are trivial to engineer (attacker sends both colliding requests back-to-back or even from two coordinated unprivileged accounts), and the map access pattern is unguarded against it, this is realistically repeatable, though timing-dependent (a race window bounded by node response latency).

### Recommendation
Key `savedCallbacks` by a composite of `(sender, MessageId)` similar to `RequestCache`'s `globalId{sender, id}`, and reject/report collisions instead of silently overwriting (e.g., return an error to the second caller if an entry already exists for that key, as `requestcache.go` does with `"request already exists"`). Additionally, when matching a node response back to a saved callback, verify that the response corresponds to the same sender/request that registered the callback, not just the raw `MessageId`.

### Proof of Concept
Go handler-level integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build two signed `api.Message`s with the same `DonId` and same `MessageId` (`"dup-id"`) but signed by two different private keys (`keyA`, `keyB`), each with distinct payload topics for identifiability.
2. Concurrently call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` and `handler.HandleLegacyUserMessage(ctx, msgB, cbB)` from two goroutines (use a `sync.WaitGroup`/barrier to maximize overlap).
3. After both calls return, lock `handler.mu` and assert `len(handler.savedCallbacks) == 1` with entry `"dup-id"` — showing one registration silently overwrote the other (no error returned from either call).
4. Construct a valid node response `resp` for `msgA` (using `hc.ValidatedResponseFromMessage(msgA)`), and call `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
5. Assert on both `cbA.Wait(ctx)` and `cbB.Wait(ctx)`: expect that whichever callback ended up stored last in the map (e.g., `cbB`) receives A's response payload, while the other callback (`cbA`) times out/never resolves — demonstrating the cross-user delivery and lost-response conditions.

### Citations

**File:** core/services/gateway/gateway.go (L250-273)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-265)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
	start := time.Now()
	switch msg.Body.Method {
	case MethodWebAPITrigger:
		err = h.handleWebAPITriggerMessage(ctx, msg, nodeAddr)
	case MethodWebAPITarget, MethodComputeAction, MethodWorkflowSyncer:
		err = h.handleWebAPIOutgoingMessage(ctx, msg, nodeAddr)
	default:
		err = fmt.Errorf("unsupported method: %s", msg.Body.Method)
	}
	h.metrics.recordHandleDuration(ctx, time.Since(start), msg.Body.Method, err == nil)
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-63)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```
