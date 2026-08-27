### Title
Cross-user response hijacking via `MessageId` collision in `savedCallbacks` map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each in-flight user callback in `h.savedCallbacks` keyed **only** by the attacker-supplied `msg.Body.MessageId`, with no binding to sender identity or connection. Because any signed gateway request lets the caller freely choose `MessageId`, a second request that reuses (or predicts) an in-flight `MessageId` silently overwrites the first callback entry, so `handleWebAPITriggerMessage` later delivers the DON node's response for the original request to the second (attacker's) callback instead.

### Finding Description
`HandleLegacyUserMessage` validates payload shape/timestamp/method but performs no allowlisting or per-request uniqueness check on `MessageId` (the code even has a `// TODO: apply allowlist and rate-limiting here` comment at [1](#0-0) ). It then stores the callback with: [2](#0-1) 

The key is `msg.Body.MessageId`, which is fully attacker-controlled: it comes straight from the client-supplied `MessageBody.MessageId` field and is only checked for length/format in `Message.Validate()` ( [3](#0-2) ), never for uniqueness or ownership. The signature commits to the `MessageId` value chosen by the signer but does **not** prevent a different signer/request from independently choosing the exact same `MessageId` string.

When a DON node later replies, `handleWebAPITriggerMessage` pops whatever callback is currently registered under that ID and delivers the response to it, with no check that the reply corresponds to the same request/sender that originally registered the callback: [4](#0-3) 

Exploit flow: (1) Victim submits request R1 with `MessageId=X`; `savedCallbacks[X] = victimCallback`. (2) Before R1's node response arrives, attacker submits R2 with the same `MessageId=X` (using their own valid signing key — no allowlist stops this); `savedCallbacks[X]` is overwritten to `attackerCallback`. (3) The DON node's response to R1 arrives and echoes back `MessageId=X` (the ID is copied verbatim by the outgoing/response path, e.g. `ValidatedRequestFromMessage`/`ValidatedMessageFromResp` at [5](#0-4) ). (4) `handleWebAPITriggerMessage` looks up `savedCallbacks[X]`, finds the attacker's callback, and delivers the victim's response data to the attacker's HTTP connection instead of the victim's.

The only sender check performed, `msg.Body.Sender != nodeAddr` in `HandleNodeMessage` ( [6](#0-5) ), validates the DON node identity, not which user originally issued the correlated request — it does nothing to prevent this collision.

### Impact Explanation
This breaks the isolation invariant between concurrent gateway users: a response payload intended for user A's request (which may include trigger/response data tied to A's workflow context) is instead handed to user B's connection via `savedCb.SendResponse`. This matches the "cross-user response confusion" bounty impact class — unauthorized disclosure of another user's response data through the gateway.

### Likelihood Explanation
Exploitability requires only the ability to send a signed gateway request with an attacker-chosen `MessageId` — any holder of a valid ECDSA key can do this since `Message.Validate()` performs no allowlist/authorization check at this layer (explicitly deferred per the TODO). The main precondition is knowing or predicting the victim's `MessageId`; if client-side ID generation is weak/predictable, or IDs are otherwise observable (e.g., re-used from client logs, short sequential counters, or replayed IDs), collision is trivial and fully repeatable — an attacker can simply race a second request against a specific ID window.

### Recommendation
Bind saved callbacks to more than just `MessageId`: key `savedCallbacks` by a composite of `(MessageId, Sender)` or a server-generated unique correlation token, and reject/queue a `HandleLegacyUserMessage` call if an unexpired entry already exists for the same `MessageId` (return an error to the second submitter) instead of silently overwriting. Additionally, verify on response delivery that the responding node's message content correlates to the same requester context that registered the callback.

### Proof of Concept
Go test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build handler via `setupHandler(t)`.
2. Construct `msgA := triggerRequest(t, nodes[0].PrivateKey, ..., messageID="X")` signed with key A, and `cbA := hc.NewCallback()`.
3. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)` — confirm `handler.savedCallbacks["X"]` points to `cbA`.
4. Construct `msgB` with a different signer/key but the same `MessageId="X"`, and `cbB := hc.NewCallback()`.
5. Call `handler.HandleLegacyUserMessage(ctx, msgB, cbB)` — assert `handler.savedCallbacks["X"]` now points to `cbB` (overwritten).
6. Simulate DON node response corresponding to `msgA` (i.e., `hc.ValidatedResponseFromMessage(msgA)` with ID `"X"`), call `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)`.
7. Assert `cbB.Wait(ctx)` returns msgA's response payload (cross-user leak), while `cbA.Wait(ctx)` times out/never resolves — proving A's response was delivered to B's callback.

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

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/handlers/common/message_util.go (L14-32)
```go
func ValidatedMessageFromResp(resp *jsonrpc.Response[json.RawMessage]) (*api.Message, error) {
	if resp.Error != nil {
		return nil, fmt.Errorf("received error, ID: %s", resp.ID)
	}
	if resp.Result == nil {
		return nil, fmt.Errorf("response result is nil, ID: %s", resp.ID)
	}
	var msg api.Message
	err := json.Unmarshal(*resp.Result, &msg)
	if err != nil {
		return nil, err
	}
	msg.Body.MessageId = resp.ID
	err = msg.Validate()
	if err != nil {
		return nil, err
	}
	return &msg, nil
}
```
