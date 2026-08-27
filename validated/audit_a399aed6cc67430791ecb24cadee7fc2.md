### Title
Legacy gateway trigger handler lacks allowlist/rate-limiting, allowing single low-privileged sender to flood and prematurely evict other users' pending callbacks - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` accepts any signed `MethodWebAPITrigger` message, verifies only signature validity and message freshness, then unconditionally stores an entry in the shared `h.savedCallbacks` map keyed by the attacker-controlled `msg.Body.MessageId`. The code contains an explicit `// TODO: apply allowlist and rate-limiting here` comment confirming no allowlist membership check and no per-sender rate limit exist at this layer, so any holder of a valid signing keypair (not necessarily allowlisted) can flood the handler with uniquely-`MessageId`'d requests.

### Finding Description
The relevant path is: [1](#0-0) 
which shows the method-check happening right after a `// TODO: apply allowlist and rate-limiting here` comment — no allowlist or rate-limit call precedes storing the callback.

Every accepted message is unconditionally inserted into the shared map: [2](#0-1) 

`pruneCallbacks` runs on a fixed interval and, once `len(h.savedCallbacks) > MaxSavedCallbacks` (default `20000`), trims the map down to `maxSize/2` by evicting the *oldest* entries regardless of which sender created them: [3](#0-2) 

Because eviction is purely by `createdAt` age and the map is shared across all senders on the DON/handler, an attacker who is a valid-but-non-allowlisted signer can submit a burst of uniquely-`MessageId`'d `MethodWebAPITrigger` requests exceeding `MaxSavedCallbacks` within one `CallbackPruneIntervalSec` window (default 30s) or between prune cycles. This causes:
- A victim's earlier-registered, still-pending callback to be evicted before the DON node's response arrives, so `handleWebAPITriggerMessage`'s lookup at `h.savedCallbacks[msg.Body.MessageId]` ( [4](#0-3) ) finds nothing and silently drops the legitimate response — the victim's HTTP/RPC request now hangs/times out with no eventual callback.
- Unbounded memory growth for `h.savedCallbacks` between prune ticks, since insertion is not itself rate-limited or capacity-checked at write time (only reactively pruned periodically).

The `nodeRateLimiter` field (`h.nodeRateLimiter.Allow(nodeAddr)`) only guards `handleWebAPIOutgoingMessage`, which is invoked for **node-originated** outgoing messages, not for the **user-originated** `HandleLegacyUserMessage` path ( [5](#0-4) ). No equivalent limiter or allowlist check gates `HandleLegacyUserMessage`.

By contrast, the newer v2 trigger handler explicitly performs `authorizeRequest` and `checkRateLimit` (backed by `userRateLimiter limits.RateLimiter`) before registering a callback: [6](#0-5) , confirming the legacy handler's gap is a known deficiency relative to the newer design rather than an intentional safe design.

### Impact Explanation
This is a Denial-of-Service / availability issue scoped to the legacy WebAPI capabilities gateway handler: a single unprivileged (but validly-keyed) sender can cause premature eviction of other users' pending callbacks on the same handler/DON, resulting in lost responses for victim requests and increased gateway memory pressure. This matches Chainlink's "Denial of Service" bounty impact class (degraded/lost service availability for legitimate node/gateway users), not a fund-loss or authentication-bypass class, since no secret disclosure or authorization bypass of DON/job execution occurs.

### Likelihood Explanation
Preconditions are low: only a valid signing keypair is required (per the code's own TODO, no allowlist membership is enforced), and the attacker does not need any special role beyond being able to sign and submit `MethodWebAPITrigger` gateway messages. The attack requires generating a volume of distinct `MessageId`s exceeding `MaxSavedCallbacks` (default 20000) or otherwise timed to arrive within a prune window before a victim's response returns — feasible for an automated script and repeatable indefinitely since no request-volume throttling exists at this layer.

### Recommendation
Implement the TODO: add per-sender/per-key allowlist and rate-limiting (mirroring `nodeRateLimiter`/`limits.RateLimiter` patterns used elsewhere in the codebase, e.g. v2's `userRateLimiter`) before message acceptance in `HandleLegacyUserMessage`, and cap or fairly partition `h.savedCallbacks` capacity per sender rather than using a single global LRU-by-age eviction, so one sender cannot starve another sender's pending callback slot.

### Proof of Concept
Go handler-level integration test plan (`core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` with `MaxSavedCallbacks` set low (e.g., 10) and `CallbackPruneIntervalSec` short for test speed.
2. Register a "victim" callback by calling `HandleLegacyUserMessage` once with a distinct, valid signature and `MessageId = "victim-1"`, capturing its `handlers.Callback` mock to assert `SendResponse` is eventually called.
3. Immediately call `HandleLegacyUserMessage` N times (N > MaxSavedCallbacks) with distinct valid signatures (simulating an unrelated, non-allowlisted signer) and distinct `MessageId`s, each a legitimate `MethodWebAPITrigger` payload with a fresh timestamp.
4. Manually invoke `h.pruneCallbacks()` (or wait for the ticker).
5. Assert that `h.savedCallbacks["victim-1"]` no longer exists (eviction occurred) even though the victim's node response has not yet arrived — demonstrating that `handleWebAPITriggerMessage` for the eventual DON response to `"victim-1"` will find `found == false` and silently fail to deliver `SendResponse` to the victim's callback mock.
6. Add a second assertion/test verifying no error, rejection, or rate-limit response is returned to the flooding sender at any point in `HandleLegacyUserMessage`, confirming absence of allowlist/rate-limit enforcement at this layer.

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
```
