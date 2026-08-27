### Title
Unprivileged client can overwrite/hijack another user's pending gateway callback via attacker-chosen `MessageId` - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The legacy WebAPI gateway trigger handler (`handler.HandleLegacyUserMessage`) stores each pending user request in a map keyed **only** by the client-supplied `msg.Body.MessageId`, with no check for an existing entry and no binding to the requester's identity, before forwarding the request to DON nodes and later resolving it via `handleWebAPITriggerMessage`. This mirrors the report's root cause: a value used to correlate a "record" (there, `pubCount`; here, the saved callback slot) is written/finalized directly from external, attacker-influenced input without protecting against a second write from a different actor arriving in the same window, causing a mismatch between who initiated a request and who receives its result.

### Finding Description
`HandleLegacyUserMessage` unconditionally stores the callback under the client-supplied ID: [1](#0-0) 

Unlike the newer `RequestCache` implementation, which keys entries by `{sender, MessageId}` and explicitly rejects a duplicate ID with `"request already exists"`: [2](#0-1) 

the legacy `savedCallbacks` map in `handler.go` is keyed by `MessageId` alone: [3](#0-2) 

When a DON node later responds, the response is matched purely by `MessageId` and delivered to whatever callback currently occupies that slot: [4](#0-3) 

Because `MessageId` originates from the incoming request body (`api.MessageBody.MessageId`) and is validated only for length/format, not uniqueness or ownership, an unprivileged second requester (or the same requester replaying/racing a request) can submit a request carrying the identical `MessageId` as another client's still-in-flight request. This silently overwrites `h.savedCallbacks[msg.Body.MessageId]` (line 412), discarding the original caller's `Callback` reference. When the DON node eventually replies using that `MessageId`, `handleWebAPITriggerMessage` delivers the response to whichever callback is currently stored — potentially the attacker's — while the original requester's HTTP call hangs until timeout with no response ever delivered.

This is directly analogous to the Lens `pubCount`/comment-overwrite bug class: a shared, externally-influenced counter/ID is used to key a to-be-finalized record, and a second actor's write in the intervening window silently clobbers the first, leading to response/content confusion between two different callers.

### Impact Explanation
- Cross-user response confusion: an attacker can cause the response to a victim's web-API trigger request to be delivered into the attacker's own HTTP connection/callback (or vice versa), because slot ownership is not verified.
- Denial of service: the victim's original request effectively vanishes from `savedCallbacks`, receiving no response until the request context times out, degrading the internet-facing gateway path for other users.
- No smart-contract or fund-loss component here (this is a Go node service), but the structural bug class — unauthorized overwrite of a pending record before it is finalized/read — is the same one described in the report.

### Likelihood Explanation
Reaching this path requires only sending two `web_api_trigger` requests to the gateway's public-facing endpoint with colliding `MessageId` values while the first is still pending — no privileged role, EI credentials, or session is required, since `HandleLegacyUserMessage` is reached from `gateway.ProcessRequest` for any legacy-format inbound request. `MessageId` is fully attacker-controlled and only constrained by `MessageIdMaxLen` / non-null-terminator checks in `Message.Validate()`, with no uniqueness or per-sender scoping enforced at this specific handler, making collision trivial to construct (an attacker doesn't need to guess anything — they simply reuse a known/observed ID, e.g., their own earlier request, or brute-force short IDs against concurrent unrelated traffic).

### Recommendation
Key `savedCallbacks` (and its lookup in `handleWebAPITriggerMessage`) by a composite of `(Sender, MessageId)` as `RequestCache` already does, and reject/queue rather than silently overwrite when an entry for that key already exists, mirroring the `"request already exists"` guard in `core/services/gateway/handlers/common/requestcache.go`. Consider migrating the legacy WebAPI trigger path onto the existing `RequestCache` abstraction instead of maintaining a parallel, weaker `map[string]*savedCallback`.

### Proof of Concept
1. Attacker sends a legacy `web_api_trigger` request to the gateway with `Body.MessageId = "X"`, `Body.Sender = attacker`. This calls `HandleLegacyUserMessage`, storing `savedCallbacks["X"] = attackerCallback` (handler.go:412) and forwarding the request to all DON members (handler.go:417-419).
2. Before the DON responds, a second, unrelated client sends its own trigger request that happens to (or is made to) also use `MessageId = "X"` (e.g., replaying an observed ID from network traffic, or simple collision under load). This overwrites `savedCallbacks["X"]` with the victim's callback, silently discarding the attacker's own registered callback — or vice versa, depending on ordering.
3. Whichever DON member responds first with `MessageId = "X"` triggers `handleWebAPITriggerMessage`, which looks up `savedCallbacks["X"]` and delivers the response to whatever callback is currently stored there (handler.go:150-159) — not necessarily the caller who is actually waiting on that specific DON round-trip, demonstrating the cross-user response/ownership confusion. [1](#0-0) [4](#0-3) [5](#0-4)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-61)
```go
type handler struct {
	services.StateMachine
	config          HandlerConfig
	don             handlers.DON
	donConfig       *config.DONConfig
	savedCallbacks  map[string]*savedCallback
	mu              sync.Mutex
	lggr            logger.Logger
	httpClient      network.HTTPClient
	nodeRateLimiter *ratelimit.RateLimiter
	wg              sync.WaitGroup
	stopCh          services.StopChan
	metrics         *metrics
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-76)
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
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```
