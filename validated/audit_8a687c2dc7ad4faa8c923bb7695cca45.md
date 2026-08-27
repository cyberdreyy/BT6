### Title
Unauthenticated MessageId collision causes silent savedCallbacks overwrite and cross-user response confusion - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`(*handler).HandleLegacyUserMessage` stores pending callbacks keyed only by `msg.Body.MessageId` in `h.savedCallbacks`, with no existence check before the write, unlike `common.RequestCache.NewRequest` which keys by `{sender, id}` and explicitly rejects duplicates with `"request already exists"`. An attacker who submits a request with a `MessageId` matching another in-flight (legitimate) request's ID silently overwrites that entry, orphaning the original caller's callback and redirecting the eventual node response to the attacker's callback.

### Finding Description
`HandleLegacyUserMessage` validates payload shape/timestamp/method but performs no uniqueness check on `msg.Body.MessageId` before writing: [1](#0-0) 
This directly overwrites any existing map entry under the same key without checking `found` first, in contrast to `common.RequestCache.NewRequest`, which keys pending requests by `globalId{sender, id}` and returns `"request already exists"` on collision: [2](#0-1) 

Critically, the key used by `savedCallbacks` is **only** `msg.Body.MessageId` — it does not incorporate the sender/signer identity — while `RequestCache`'s key explicitly includes `sender`. This means two different, unrelated senders (one legitimate, one attacker) can collide on the same `MessageId` and the second write wins, with the first caller's `savedCallback` becoming unreachable (leaked, orphaned goroutine/channel until the eventual node response for that MessageId is misdirected). When `handleWebAPITriggerMessage` later receives the node's response for that `MessageId`, it looks up and deletes by `MessageId` alone and delivers the response to whichever callback is currently stored: [3](#0-2) 

This is a genuine violation of the request-uniqueness/isolation invariant enforced elsewhere in the same package tree (`RequestCache`, and analogously `confidentialrelay/handler.go`'s `newActiveRequest` and `vault/handler.go`'s `newActiveRequest`, both of which explicitly reject duplicate request IDs): [4](#0-3) [5](#0-4) 

I was unable to fully trace, within the available tool budget, the exact upstream HTTP/JSON-RPC route and any DON-side allowlist/signature verification step that occurs before `HandleLegacyUserMessage` is invoked (e.g., in `core/services/gateway/gateway.go`) to confirm whether `MessageId` is attacker-freely-choosable end-to-end or whether some upstream layer already deduplicates/binds it to sender identity before reaching this handler. The `MessageId` field itself is not validated for uniqueness or tied to sender in `ValidatedRequestFromMessage`/`api.Message.Validate` as far as observed. This uncertainty should be resolved by tracing the full request path in the actual repository before treating this as conclusively exploitable end-to-end.

### Impact Explanation
If confirmed exploitable end-to-end (i.e., an external/unauthenticated caller can freely choose `MessageId` and have it reach this handler while a legitimate request with the same ID is still pending), the impact is **cross-user response confusion**: an attacker can hijack delivery of another user's trigger response by guessing/predicting or racing a `MessageId`, or at minimum cause denial-of-service on a specific in-flight legacy web API trigger request (the legitimate caller's callback is silently dropped and will only resolve via the timeout path, if any exists at this layer — note `savedCallback` here has no timeout timer, unlike `RequestCache`'s `pendingRequest`, so the orphaned callback may never be resolved and could hang until process shutdown, since `pruneCallbacks` only runs periodically to reclaim old entries, not to signal the caller). This matches a "request impersonation / cross-user response confusion" class impact.

### Likelihood Explanation
Preconditions: an attacker needs the ability to submit a legacy web API trigger message (method `MethodWebAPITrigger`) with an attacker-chosen `MessageId` that collides with a currently in-flight request from another party, within the pruning window (`CallbackMaxAgeSec`, default 120s). Whether this is remotely feasible depends on whether `MessageId` values are attacker-controlled and unpredictable/predictable, and whether any upstream authentication/signature step in `gateway.go` restricts or namespaces `MessageId` per sender before reaching this map — this could not be fully confirmed with the tools available in this session.

### Recommendation
Scope the `savedCallbacks` map key by both sender and `MessageId` (mirroring `RequestCache`'s `globalId{sender, id}`), and reject (or fail loudly) on duplicate keys instead of silently overwriting, e.g.:
```go
h.mu.Lock()
key := savedCallbackKey{sender: msg.Body.Sender, id: msg.Body.MessageId}
if _, exists := h.savedCallbacks[key]; exists {
    h.mu.Unlock()
    return callback.SendResponse(... "duplicate message id" ...)
}
h.savedCallbacks[key] = &savedCallback{...}
h.mu.Unlock()
```
Also consider adding a per-entry timeout (like `RequestCache`'s `timeoutTimer`) so orphaned/overwritten callbacks are not left hanging indefinitely.

### Proof of Concept
Go unit test plan (to be placed in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct handler via `NewHandler` with a mock `handlers.DON`.
2. Build two valid `api.Message`s with identical `Body.MessageId` but different `Body.Sender`/`Body.DonId`/signatures, both with `Body.Method = MethodWebAPITrigger` and valid `TriggerRequestPayload` (fresh `Timestamp`).
3. Call `h.HandleLegacyUserMessage(ctx, msg1, callback1)` then `h.HandleLegacyUserMessage(ctx, msg2, callback2)` back-to-back.
4. Assert `len(h.savedCallbacks) == 1` after both calls (map did not grow to 2), and `h.savedCallbacks[msg1.Body.MessageId].Callback == callback2` (only the second callback is reachable; `callback1` is unreachable/orphaned).
5. Simulate a node response for that `MessageId` via `h.HandleNodeMessage` and assert only `callback2.Wait(ctx)` resolves while `callback1.Wait(ctx)` never receives a response (times out), demonstrating cross-user response confusion.
6. Contrast with `common.RequestCache`'s `TestRequestCache_MaxSize`/duplicate-key test at `core/services/gateway/handlers/common/requestcache_test.go` lines 125-141, where the second `NewRequest` call with a colliding key returns an error and the cache is unaffected — showing this handler's `savedCallbacks` map lacks the equivalent guard.

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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-374)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-472)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
```
