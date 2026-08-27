### Title
Cross-User Response Hijacking via Unvalidated MessageId Overwrite in Gateway Dummy Handler - (File: core/services/gateway/handlers/handler.dummy.go)

### Summary
The `dummyHandler` used by the gateway (`core/services/gateway/handlers/handler.dummy.go`, wired via `core/services/gateway/handler_factory.go`) stores a per-request callback keyed only by the client-supplied `msg.Body.MessageId`, with no uniqueness check and no binding to the requesting sender. A second unprivileged request that reuses an in-flight `MessageId` silently overwrites the first request's stored callback, so the eventual DON response is delivered to the second (attacker-controlled) caller instead of the original requester.

### Finding Description
`HandleLegacyUserMessage` does: [1](#0-0) 
It writes directly into `d.savedCallbacks[msg.Body.MessageId]` without checking whether an entry already exists for that ID (unlike the vault handler's `newActiveRequest`, which explicitly rejects duplicate IDs: [2](#0-1) , or `RequestCache.NewRequest`, which scopes the cache key by `{sender, id}` and rejects existing entries: [3](#0-2) ).

When the DON node eventually replies, `HandleNodeMessage` looks the callback up purely by `msg.Body.MessageId` and delivers the response to whatever callback is currently stored there: [4](#0-3) 
The only sender check performed (`nodeAddr != msg.Body.Sender`) validates that the *node* answering matches the expected DON node — it does nothing to verify that the *callback* stored under that MessageId still belongs to the original client who issued the request. If a second unprivileged client submits a request using the same `MessageId` while the first is still pending, the map entry (and therefore the eventual node response) is silently reassigned to the second client's callback, and the original caller never receives a response (or times out), while the attacker receives a response destined for someone else's request.

This directly parallels the "uncontained/uncontrolled batch-processing" class of bug in the report: a single caller-controlled value (Set component in the original report; `MessageId` here) that is trusted without per-caller isolation can silently corrupt or redirect the outcome of another party's operation.

### Impact Explanation
An unprivileged HTTP caller to the gateway can hijack the response destined for another user's in-flight legacy request simply by guessing or predicting (or brute-forcing, since `MessageId` is client-supplied and not required to be globally unique or high entropy beyond length limits: [5](#0-4) ) the `MessageId` of a pending request and submitting a colliding request. This is a cross-user response confusion: sensitive response payloads intended for User A could be delivered to User B, and User A's original request could silently fail/hang.

### Likelihood Explanation
Exploitability depends on: (1) the `dummyHandler` path being reachable in a running deployment — it is registered in `handler_factory.go` as a handler type, so any DON configured to use it is exposed; (2) an attacker being able to predict or brute-force another in-flight `MessageId`. Since `MessageId` is entirely attacker/client chosen with no server-side uniqueness enforcement, an attacker who also controls (or colludes with) their own client can trivially create a collision by choosing the same fixed string used by another integration/test client, or by racing many guesses during a window when high-traffic/well-known MessageIds are in flight. This is a low-complexity, no-privilege attack once the handler is in use.

### Recommendation
- Scope the callback/response cache key by `(sender, MessageId)` rather than `MessageId` alone, mirroring `RequestCache`'s `globalId{sender, id}` pattern.
- Reject (rather than silently overwrite) new requests that collide with an existing pending `MessageId`, mirroring the vault handler's `newActiveRequest` duplicate-ID check.
- Verify, on response delivery, that the responding message's sender/receiver metadata matches the original request's caller identity before dispatching to the saved callback.

### Proof of Concept
1. Client A sends a legacy gateway request with `Body.MessageId = "X"` and `Body.Sender = A`; `dummyHandler.HandleLegacyUserMessage` stores `savedCallbacks["X"] = callbackA` and forwards the JSON-RPC request (`ID: "X"`) to DON members.
2. Before the DON responds, Client B (attacker) sends a legacy request with the same `Body.MessageId = "X"` and `Body.Sender = B`. `HandleLegacyUserMessage` overwrites `savedCallbacks["X"] = callbackB` with no conflict check.
3. The DON node responds with `resp.ID = "X"`. `HandleNodeMessage` looks up `savedCallbacks["X"]`, finds `callbackB`, and delivers the response there — Client A's callback is orphaned (never resolved) or times out, while Client B receives a response tied to Client A's original request/method context. [6](#0-5)

### Citations

**File:** core/services/gateway/handlers/handler.dummy.go (L62-67)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
	params, err := json.Marshal(msg)
```

**File:** core/services/gateway/handlers/handler.dummy.go (L84-108)
```go
func (d *dummyHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	var msg api.Message
	err := json.Unmarshal(*resp.Result, &msg)
	if err != nil {
		return err
	}
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
	return nil
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
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

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/handler_factory.go (L1-1)
```go
package gateway
```
