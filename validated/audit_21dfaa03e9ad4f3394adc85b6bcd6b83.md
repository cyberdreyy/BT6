### Title
Unauthenticated flood of Web API trigger requests can evict other users' pending gateway callbacks - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The gateway's Web API trigger handler stores every incoming user trigger request in a single shared, DON-wide map (`savedCallbacks`) with a global capacity (`MaxSavedCallbacks`, default 20000). There is no per-sender quota, authentication, or rate limiting applied before an entry is admitted, and the code explicitly flags this gap. When the map grows past the limit, the periodic pruning routine evicts the oldest half of entries indiscriminately — including other users' still-pending, legitimate callbacks — silently dropping their responses. This mirrors the reported `VotingEscrow` bug class: a shared destination-scoped resource with a global cap that any unprivileged actor can cheaply fill, griefing unrelated victims out of receiving what they are owed (there, delegated votes; here, their trigger response).

### Finding Description
`HandleLegacyUserMessage` accepts a `MethodWebAPITrigger` message from any external caller and unconditionally inserts it into the shared `h.savedCallbacks` map keyed only by `msg.Body.MessageId`, with no check of who the sender is and no per-sender limit: [1](#0-0) [2](#0-1) 

The code comment itself acknowledges the missing control: `// TODO: apply allowlist and rate-limiting here` [3](#0-2) .

The only bound on the map's size is enforced asynchronously, once every `CallbackPruneIntervalSec` (default 30s), by `pruneCallbacks`, which first removes expired entries and then, if the map is still larger than `MaxSavedCallbacks`, sorts remaining entries by `createdAt` and deletes the oldest half — regardless of whether those entries belong to a different, unrelated caller who is still legitimately waiting for a node response: [4](#0-3) 

Because insertion has no synchronous cap check (unlike `VotingEscrow`'s `require` in `_moveAllDelegates`, which reverts once `MAX_DELEGATES` is hit), an unprivileged sender can push the shared map arbitrarily far past `MaxSavedCallbacks` within a single prune interval simply by submitting many distinct `MessageId`s, each essentially free to generate (no proof-of-work, no per-owner accounting, no auth check). On the next prune, half of all entries — including any unrelated legitimate victim's callback — are deleted, and that victim's response is dropped without notification (`handleWebAPITriggerMessage` will find no `savedCallbacks` entry for its `MessageId` and simply drop the node's response): [5](#0-4) 

This is the same shape of bug reported for `VotingEscrow`: a shared, globally-capped structure (`MAX_DELEGATES` per `dst` / `savedCallbacks` DON-wide) that any unprivileged actor can cheaply saturate to grief unrelated third parties out of a result they are entitled to (delegated votes / callback response), and the griefing can be repeated indefinitely.

### Impact Explanation
An unauthenticated external client can grief arbitrary other users of the same DON's gateway by causing their legitimate, in-flight Web API trigger requests to be silently evicted and never receive a response, forcing them to retry or fail their workflow step. This is a denial-of-service/griefing impact against normal users of the gateway with no profit motive required from the attacker, consistent with the "Griefing" impact category of the analog report. Because insertion of entries is unbounded and unauthenticated, a single unprivileged actor can trigger this at will and repeat it continuously.

### Likelihood Explanation
Likelihood is high for any deployment where `HandleLegacyUserMessage` is externally reachable without an upstream allowlist/rate-limit already applied (the code's own TODO indicates this control is not yet implemented at this layer), since generating many distinct `MessageId`s and issuing many trigger requests requires no special privilege, staking, or cost. The attack only needs to occur faster than the `CallbackPruneIntervalSec` window (default 30s) to guarantee eviction of concurrently pending legitimate entries.

### Recommendation
- Enforce a synchronous per-sender/per-owner quota on `savedCallbacks` insertion (reject new entries once a sender-scoped or global limit is reached) instead of relying solely on periodic, sender-agnostic eviction.
- Implement the allowlist/rate-limiting referenced in the TODO comment before admitting entries into the shared map.
- When evicting due to capacity pressure, prefer evicting/reporting in a way that does not silently drop a legitimate caller's in-flight response (e.g., send an explicit error/timeout callback rather than a silent delete), and consider fairness across senders rather than pure oldest-first eviction.

### Proof of Concept
1. An external, unauthenticated client repeatedly calls the gateway's legacy Web API trigger endpoint (`MethodWebAPITrigger`) with a large number of distinct, freshly-generated `MessageId`s in quick succession, well before the next `CallbackPruneIntervalSec` tick.
2. Each request is admitted unconditionally into `h.savedCallbacks` per `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:411-420`), since there is no per-sender check or synchronous cap enforcement.
3. Meanwhile, a legitimate user (analogous to "Alex"/"Maya" in the report) submits a normal trigger request whose callback is also inserted into the same shared map.
4. When `pruneCallbacks` next runs (`core/services/gateway/handlers/capabilities/handler.go:299-338`) and finds the map above `MaxSavedCallbacks`, it deletes the oldest half of all entries by `createdAt`, which — if the legitimate user's request happens to be among the older half relative to the attacker's flood — silently removes it.
5. When the node eventually responds to the legitimate user's trigger, `handleWebAPITriggerMessage` (`core/services/gateway/handlers/capabilities/handler.go:148-162`) finds no matching entry in `savedCallbacks` and drops the response, leaving the legitimate user's request unanswered.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-338)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}

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

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
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
