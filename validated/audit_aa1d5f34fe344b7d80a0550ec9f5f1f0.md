### Title
Unauthenticated flood of `web_api_trigger` messages evicts legitimate users' pending `savedCallback` entries, causing cross-user response loss - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores every incoming trigger message's callback in a single shared, unbounded-until-pruned `map[string]*savedCallback` keyed only by attacker-controlled `MessageId`, with no per-caller authentication, quota, or isolation. Because `pruneCallbacks()` evicts the globally oldest half of entries once the map exceeds `MaxSavedCallbacks` (20000), an unauthenticated flood of uniquely-keyed trigger requests can force eviction of a legitimate, still-in-flight victim entry before the corresponding node response arrives.

### Finding Description
`HandleLegacyUserMessage` is reachable from the gateway's public HTTP entrypoint `gateway.ProcessRequest` → `h.HandleLegacyUserMessage(ctx, msg, callback)` [1](#0-0) . The code path explicitly documents the missing control: `// TODO: apply allowlist and rate-limiting here` right before dispatching on `MethodWebAPITrigger` [2](#0-1) . The only checks performed are payload decoding, a `Timestamp`/staleness check, method whitelisting, and `common.ValidatedRequestFromMessage`, none of which require a specific credential, session, or a signature tied to any privileged identity — `ValidatedRequestFromMessage` only checks that `MessageId`/`Method` are non-empty [3](#0-2) . Any caller who can reach the gateway's user port can therefore submit arbitrarily many distinct `MessageId`s.

Each accepted message is stored unconditionally in the shared map: [4](#0-3) 

`pruneCallbacks()` runs on a periodic ticker (`CallbackPruneIntervalSec`, default 30s) and, once the map exceeds `MaxSavedCallbacks` (default 20000), sorts *all* entries by `createdAt` and deletes the oldest half — regardless of which caller created them: [5](#0-4) 

Because the map is global to the handler/DON (not partitioned per sender/IP/key), an attacker flooding the endpoint with >`MaxSavedCallbacks` uniquely-keyed `web_api_trigger` requests forces the next prune cycle to delete legitimate older entries, including a victim's still-pending `savedCallback`. When the real DON node eventually replies for that evicted `MessageId`, `handleWebAPITriggerMessage` finds nothing in the map (`found == false`) and silently drops the response: [6](#0-5) 

The victim's HTTP client is left blocked on `callback.Wait(ctx)` in `gateway.ProcessRequest` until it times out with `RequestTimeoutError` [7](#0-6) .

### Impact Explanation
This is an unauthenticated, cross-user denial-of-service / response-hijacking-by-deletion: any unprivileged network client can starve/evict other users' pending gateway trigger requests purely by volume, causing their legitimate requests to silently time out. This matches the "resource starvation / cross-user isolation break" bounty impact class rather than key disclosure or fund movement, but it is a genuine availability and isolation failure reachable without any credential, aggravated by the explicit unaddressed `TODO` for rate-limiting/allowlisting on this exact path.

### Likelihood Explanation
No authentication, session, or role is required — only network access to the gateway's user-facing HTTP port and the ability to construct syntactically valid `api.Message` JSON-RPC requests with unique `MessageId`s (trivial, e.g. random UUIDs) and a non-stale `Timestamp`. `NodeRateLimiter` only limits node→gateway messages via `nodeRateLimiter.Allow(nodeAddr)` in `handleWebAPIOutgoingMessage`, not user→gateway ingestion in `HandleLegacyUserMessage` [8](#0-7) , so there is no throttling on the vulnerable path. The attack is fully repeatable and only bounded by the attacker's ability to generate HTTP requests (>20000 within roughly one prune interval to guarantee eviction, though partial floods still degrade the buffer over time).

### Recommendation
Add per-caller/IP rate limiting and/or an allowlist on `HandleLegacyUserMessage` as the existing `TODO` indicates, and consider partitioning `savedCallbacks` (or applying per-sender quotas) so that no single unauthenticated sender can consume more than a bounded share of the callback map, preventing eviction of other callers' pending entries.

### Proof of Concept
1. In `handler_test.go`, construct a `handler` with `MaxSavedCallbacks = N` (small, e.g. 10) and `CallbackPruneIntervalSec` irrelevant (call `pruneCallbacks()` directly).
2. Insert one "victim" `savedCallback` with `createdAt = now` and a mock `Callback` that records whether `SendResponse` was called.
3. Insert `N` additional synthetic `savedCallback` entries with `createdAt` timestamps all *after* the victim's (simulating attacker flood arriving right after), each keyed by unique random `MessageId`s.
4. Call `h.pruneCallbacks()`.
5. Assert the victim's entry has been deleted from `h.savedCallbacks` (`_, found := h.savedCallbacks[victimID]; !found`) even though it was the legitimate earliest request, and separately confirm that a subsequent call to `handleWebAPITriggerMessage` for the victim's `MessageId` returns `nil` without invoking `SendResponse` — i.e., the victim's response is silently dropped.

### Citations

**File:** core/services/gateway/gateway.go (L264-273)
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
```

**File:** core/services/gateway/gateway.go (L278-285)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L314-334)
```go
	// If there are still too many callbacks, sort them by creation time and remove the oldest ones.
	maxSize := h.config.MaxSavedCallbacks
	var evicted int
	if len(h.savedCallbacks) > maxSize {
		type entry struct {
			id        string
			createdAt time.Time
		}
		entries := make([]entry, 0, len(h.savedCallbacks))
		for id, cb := range h.savedCallbacks {
			entries = append(entries, entry{id, cb.createdAt})
		}
		sort.Slice(entries, func(i, j int) bool {
			return entries[i].createdAt.Before(entries[j].createdAt)
		})
		// Trim to maxSize/2 to avoid sorting the list too frequently.
		for _, e := range entries[:len(entries)-maxSize/2] {
			delete(h.savedCallbacks, e.id)
			evicted++
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
```go
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/message_util.go (L82-104)
```go
// ValidatedRequestFromMessage converts a legacy Gateway Message to a JSON-RPC request
func ValidatedRequestFromMessage(msg *api.Message) (*jsonrpc.Request[json.RawMessage], error) {
	if msg == nil {
		return nil, errors.New("nil message")
	}
	if msg.Body.MessageId == "" {
		return nil, errors.New("message ID is empty")
	}
	if msg.Body.Method == "" {
		return nil, errors.New("method is empty")
	}
	params, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal message: %w", err)
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	return req, nil
```
