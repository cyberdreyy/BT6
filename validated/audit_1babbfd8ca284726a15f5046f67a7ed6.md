### Title
Unauthenticated volumetric flood of `web_api_trigger` requests causes premature eviction of a victim's pending `savedCallbacks` entry, silently dropping node responses (targeted denial-of-response) - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.pruneCallbacks` evicts the oldest half of `savedCallbacks` purely by `createdAt` order whenever the map exceeds `MaxSavedCallbacks`, with no per-sender or per-identity protection. Because `HandleLegacyUserMessage` accepts any self-signed message with no allowlist/rate-limit check at this layer (explicit `// TODO: apply allowlist and rate-limiting here`), an unauthenticated attacker can flood the gateway with enough valid-but-bogus trigger requests to push a specific in-flight victim's callback entry out of the map before the DON node's response arrives, causing that response to be silently discarded.

### Finding Description
`HandleLegacyUserMessage` [1](#0-0)  only validates payload decoding, message staleness, and method name before storing the callback: [2](#0-1) 

There is no sender allowlist or per-sender rate limit enforced at this layer — that check only happens later, inside the workflow node's own `trigger.processTrigger` (`allowedSenders`/`rateLimiter.Allow`), which runs after the gateway has already stored the pending callback and dispatched the request to all DON members. Any caller who can produce a validly-formed, self-signed `api.Message` (any ECDSA key works — `msg.Validate()` only checks signature well-formedness, not membership in an allowlist) can reach this code path.

`pruneCallbacks` then evicts by pure FIFO order of `createdAt`, keeping only the newest half of the map once it exceeds `MaxSavedCallbacks` (default 20000): [3](#0-2) 

Note the exploit direction is the opposite of what the question describes: entries are sorted ascending by `createdAt` and `entries[:len(entries)-maxSize/2]` (the *oldest* half) is deleted, while the *newest* half survives. So an attacker does not need to guess or backdate timestamps to be "older" than the victim — they simply need to send a sufficient volume of requests *after* the victim's request, since the victim's genuinely-earlier entry will always be older than the flood and thus preferentially evicted once the map exceeds capacity. No guessing of `createdAt` is required at all; it only requires knowing (or blindly always assuming) that some other legitimate request is currently in flight.

Once the victim's `savedCallback` entry is evicted, when the legitimate node response eventually arrives, `handleWebAPITriggerMessage` looks it up and finds nothing: [4](#0-3) 
The response is silently dropped, and the victim's original `gateway.ProcessRequest` call (blocked on `callback.Wait(ctx)`) receives no response — the caller experiences a full request timeout / `RequestTimeoutError` even though the DON correctly processed and answered the trigger: [5](#0-4) 

### Impact Explanation
This is an unauthenticated denial-of-response against arbitrary in-flight gateway users: legitimate `web_api_trigger` callers can have their genuine node responses discarded due to unrelated attacker traffic, forcing a timeout instead of the correct result. This falls under the "Denial of Service" / griefing impact class for gateway availability — it does not grant fund movement or credential disclosure, but it reliably denies legitimate users their expected responses without requiring any privileged access.

### Likelihood Explanation
Exploitability requires only the ability to send HTTP requests to the gateway's public endpoint with a self-signed `api.Message` (no allowlisted key needed at the gateway layer) referencing any known/guessable `DonId`. The attacker must sustain enough volume (default: >10,000 requests, i.e. `MaxSavedCallbacks/2`) within the `CallbackPruneIntervalSec` (default 30s) / `CallbackMaxAgeSec` (default 120s) window to outrun a targeted victim's request before the DON responds. This is bounded by HTTP-layer request limits (`MaxRequestBytes`, `ReadTimeoutMillis`, etc.) but no per-sender quota exists at this specific code path to stop it — the TODO comment confirms this gap is a known, unaddressed limitation. Repeatable and fully attacker-controlled from an unauthenticated position.

### Recommendation
Enforce per-sender rate limiting/allowlisting at the gateway `HandleLegacyUserMessage` entry point (not only downstream inside node-side trigger handlers), and/or change `pruneCallbacks` eviction to be sender-aware (e.g., quota per sender) rather than a single global FIFO, so that a flood from one sender cannot displace another sender's pending callback.

### Proof of Concept
Go handler-level integration test plan (`core/services/gateway/handlers/capabilities/handler_test.go`):
1. Configure `handler` with small `MaxSavedCallbacks` (e.g., 10) to make the test tractable.
2. Call `HandleLegacyUserMessage` once for a "victim" message (`messageId = "victim"`), capturing its callback.
3. Immediately loop calling `HandleLegacyUserMessage` with `> MaxSavedCallbacks/2` freshly self-signed attacker messages (each valid, distinct `MessageId`, distinct ephemeral ECDSA key, no relation to the DON allowlist).
4. Invoke `handler.pruneCallbacks()` directly.
5. Assert `h.savedCallbacks["victim"]` is no longer present (evicted) while the attacker's newer entries remain.
6. Simulate the DON node responding for `"victim"` via `HandleNodeMessage`; assert the victim's callback never receives `SendResponse` (i.e., `handleWebAPITriggerMessage` silently no-ops because `found == false`), demonstrating the response is dropped.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-384)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
