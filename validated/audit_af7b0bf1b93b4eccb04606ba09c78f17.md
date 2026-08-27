### Title
Unauthenticated flooding of `savedCallbacks` causes premature eviction of legitimate pending callbacks in `pruneCallbacks` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` inserts a `savedCallback` into the shared `h.savedCallbacks` map for every valid trigger message it receives, with no per-sender rate limiting or allowlist check (explicitly marked as a `TODO` in the code). An unprivileged gateway client can repeatedly submit `web_api_trigger` messages with unique `MessageId`s to grow the map past `MaxSavedCallbacks`, forcing `pruneCallbacks` to evict the oldest half by `createdAt`, which can include another legitimate, still-pending caller's callback, causing that caller's `SendResponse` to never be invoked.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341-421) validates payload structure/timestamp/method, but the comment at line 384 states `// TODO: apply allowlist and rate-limiting here`, confirming there is no rate limit or allowlist enforcement on this path before a callback entry is registered: [1](#0-0) 

Every syntactically-valid request is stored in the shared map keyed by `msg.Body.MessageId`, with no bound applied at insertion time: [2](#0-1) 

`pruneCallbacks` runs on a periodic ticker (default every `CallbackPruneIntervalSec`=30s) and, if the map exceeds `MaxSavedCallbacks` (default 20000) after removing expired entries, sorts remaining entries by `createdAt` and deletes the oldest half — regardless of who owns them: [3](#0-2) 

When an entry is evicted this way, the corresponding `Callback.SendResponse` is never called. The caller's HTTP request path (`gateway.ProcessRequest` → `callback.Wait(ctx)`) is only released by `ctx.Done()` (its own request context) or an actual response arriving: [4](#0-3) [5](#0-4) 

So an evicted victim does not get a distinguishing error from the gateway due to eviction — they simply time out via their own request context, indistinguishable from any other timeout. This matches the described "silent starvation" — the victim's request hangs until its own deadline rather than getting prompt feedback, and worse, when the legitimate node response for their message eventually arrives at `handleWebAPITriggerMessage`, the lookup in `savedCallbacks` will miss (already evicted) and the response is silently dropped: [6](#0-5) 

Every unauthenticated web API caller shares the exact same `handler` instance and its single global `savedCallbacks` map/mutex per DON — there is no per-user or per-tenant partitioning of this state, so one caller's flood directly impacts another unrelated caller's pending request.

### Impact Explanation
This is a denial-of-isolation / availability issue between unprivileged callers of the same gateway DON handler: a flood of ~10,000+ distinct trigger requests (to exceed `MaxSavedCallbacks`/push eviction of the older half) can cause a legitimate, earlier-submitted request to have its callback silently discarded, so the victim never receives their trigger response and instead experiences a hang/timeout. This falls under an availability/denial-of-service impact class rather than fund loss, auth bypass, or secret disclosure — it does not grant privilege escalation or cross-user data leakage, only disruption of another user's request completion.

### Likelihood Explanation
No authentication or special role is required to invoke `HandleLegacyUserMessage` via the gateway's user-facing HTTP endpoint — the code path (`gateway.ProcessRequest` → `HandleLegacyUserMessage`) is reachable by any unauthenticated web API caller. The comment explicitly acknowledges the missing allowlist/rate-limit control, and `MessageId` values are attacker-supplied/unique per request, so filling the map to force eviction requires only volume (network bandwidth/compute), no privileged access. This is realistically achievable against a busy or resource-constrained node whose `MaxSavedCallbacks` and prune interval settings allow the map to accumulate enough entries before pruning executes. Exact severity depends on operational configuration (default `MaxSavedCallbacks` = 20000, `CallbackMaxAgeSec` = 120s), and lower-traffic legitimate load makes exploitation more feasible since fewer attacker requests are needed.

### Recommendation
- Implement the pending "TODO: apply allowlist and rate-limiting" at line 384 of `HandleLegacyUserMessage`, e.g., per-sender/per-IP rate limiting before inserting into `savedCallbacks`.
- Reject/backpressure new registrations once `savedCallbacks` is at capacity (return an explicit "server busy"/queue-full error to the new caller) instead of silently evicting older pending entries.
- Consider partitioning `savedCallbacks` or applying fairness (e.g., per-sender caps) so a single flooding sender cannot exhaust global capacity used by others.
- When evicting due to `pruneCallbacks`, invoke `SendResponse` with an explicit timeout/error payload on the evicted callback rather than silently dropping it, so victims get a clear signal instead of hanging until their own context timeout.

### Proof of Concept
Handler-level Go test plan (extends `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` with a small `MaxSavedCallbacks` (e.g., 10) and short `CallbackPruneIntervalSec` for test speed, using `setupHandler(t)` helper already present in the test file.
2. Register a "victim" callback: call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)` with a valid `web_api_trigger` payload and a unique `MessageId`; confirm it is present in `handler.savedCallbacks`.
3. "Attacker" floods: loop calling `handler.HandleLegacyUserMessage(ctx, attackerMsg_i, attackerCb_i)` with unique `MessageId`s until `len(handler.savedCallbacks) > MaxSavedCallbacks`.
4. Manually invoke `handler.pruneCallbacks()` (or wait for the ticker) and assert:
   - `handler.savedCallbacks` no longer contains the victim's `MessageId` (because it was one of the oldest).
   - Simulate the legitimate node response arriving for the victim via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`; assert `victimCb.SendResponse` was never invoked (verify by checking `victimCb.Wait(ctx)` returns a context-deadline error, not a real response, using a short test-controlled context).
5. Assert the test fails on current code (`SendResponse` never fires for the victim) demonstrating starvation, and would pass under a fix that either preserves earliest-registered legitimate callbacks or explicitly resolves evicted callbacks with an error response instead of silent drop.

### Citations

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

**File:** core/services/gateway/handlers/common/callback.go (L28-38)
```go
func (c *Callback) Wait(ctx context.Context) (handlers.UserCallbackPayload, error) {
	if !c.waitCalled.CompareAndSwap(false, true) {
		return handlers.UserCallbackPayload{}, errors.New("Wait can only be called once per Callback instance")
	}
	select {
	case <-ctx.Done():
		return handlers.UserCallbackPayload{}, ctx.Err()
	case r := <-c.ch:
		return r, nil
	}
}
```

**File:** core/services/gateway/gateway.go (L264-285)
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
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
```
