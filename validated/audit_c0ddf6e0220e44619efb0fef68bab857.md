This confirms the flow: `gateway.ProcessRequest` at [1](#0-0)  validates only the message signature/schema via `msg.Validate()` — no per-sender allowlist or quota check — before calling `h.HandleLegacyUserMessage`, which the code explicitly flags as missing rate-limiting (`// TODO: apply allowlist and rate-limiting here`).

### Title
Unauthenticated attacker can evict a victim's pending gateway callback via unbounded `savedCallbacks` flooding, causing targeted DoS - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` inserts every incoming signed legacy request into the shared `h.savedCallbacks` map keyed only by `MessageId`, with no per-sender identity, quota, or fairness tracking. `pruneCallbacks` evicts purely by global insertion order (oldest `createdAt` first) once `len(savedCallbacks) > MaxSavedCallbacks`, so any single attacker capable of submitting enough signed legacy requests can force eviction of an older, still-pending victim callback before the victim's DON response arrives.

### Finding Description
The gateway's HTTP entrypoint `gateway.ProcessRequest` only validates message shape/signature via `msg.Validate()` and routes to `h.HandleLegacyUserMessage`, as shown at [1](#0-0) . Inside `HandleLegacyUserMessage`, after basic timestamp/method checks, the handler unconditionally stores the callback keyed by `MessageId` with no rate limit or per-sender cap, explicitly marked with a TODO: [2](#0-1) .

The background pruning routine `pruneCallbacks`, triggered every `CallbackPruneIntervalSec`, first removes expired entries, then — if the map still exceeds `MaxSavedCallbacks` — sorts all remaining entries strictly by `createdAt` and deletes the oldest half, with no consideration of sender/identity: [3](#0-2) .

Since any party who can produce a valid ECDSA signature (a freely generated key, as shown in test helper `newSignedLegacyRequest`, works — there is no allowlist gating `HandleLegacyUserMessage`) can submit arbitrarily many distinct `MessageId`s each creating a fresh `savedCallbacks` entry, an attacker can submit more than `MaxSavedCallbacks - 1` fresh requests immediately after a victim's request lands. Because pruning is purely FIFO-by-creation-time and non-identity-aware, the victim's earlier, still-outstanding callback is evicted from the map before the legitimate DON response for it arrives via `handleWebAPITriggerMessage` (which looks up `h.savedCallbacks[msg.Body.MessageId]` at [4](#0-3) ). When the DON eventually responds, the entry is gone, so the response is silently dropped, and the victim's `callback.Wait(ctx)` in `gateway.ProcessRequest` at [5](#0-4)  times out and returns a `RequestTimeoutError` to the victim.

### Impact Explanation
This is a targeted denial-of-service on a specific victim's legacy gateway request: the attacker can deterministically make a chosen pending request silently fail via timeout by flooding the shared callback store, breaking the isolation/fairness invariant between unrelated gateway users. It does not lead to key/secret disclosure or fund movement, but it is a legitimate availability/DoS impact against unauthenticated multi-tenant fairness in a shared node resource.

### Likelihood Explanation
Exploitation requires only the ability to send signed legacy gateway HTTP requests — any key can sign arbitrary distinct `MessageId`s, and there is no allowlist or per-sender quota gating `HandleLegacyUserMessage` (explicitly noted as an open TODO). The attacker must win a timing race (flood enough requests before the DON responds to the victim, within `CallbackPruneIntervalSec` and before `handleWebAPITriggerMessage` for the victim fires), and `MaxSavedCallbacks` defaults to 20000, which raises the cost of the attack in production but does not eliminate it; the vulnerability is deterministic and repeatable given sufficient request volume relative to the configured `MaxSavedCallbacks`.

### Recommendation
Add per-sender (extracted signer address) quotas/rate-limiting in `HandleLegacyUserMessage` before inserting into `savedCallbacks` (the TODO already flags this), and change `pruneCallbacks` eviction to be sender-aware (e.g., enforce a max-per-sender cap or evict from the sender(s) contributing disproportionately to map growth) rather than purely global FIFO-by-age, so one identity's flood cannot displace another identity's pending entry.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with `config.MaxSavedCallbacks` set small (e.g., 4) and `CallbackMaxAgeSec` large enough to avoid expiry-based pruning interfering.
2. Call `handler.HandleLegacyUserMessage` once with a victim-signed trigger request (unique key/signature) to populate `savedCallbacks[victimID]`.
3. Loop calling `HandleLegacyUserMessage` with attacker-signed requests (fresh key per call is unnecessary — same key, distinct `MessageId`/timestamp) enough times to exceed `MaxSavedCallbacks`.
4. Directly invoke `handler.pruneCallbacks()`.
5. Assert `victimID` is no longer present in `h.savedCallbacks` (evicted), while newer attacker entries remain — demonstrating eviction is purely insertion-order-based and not sender/fairness-aware.
6. Optionally simulate the victim's DON response afterward via `handleWebAPITriggerMessage` and assert `callback.Wait` never receives a response (times out), confirming the end-to-end DoS.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-414)
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
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
