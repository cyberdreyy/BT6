### Title
Unbounded `savedCallbacks` growth allows attacker to force premature eviction of legitimate pending trigger callbacks - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` accepts any message that carries a syntactically valid ECDSA signature (any keypair, not a per-DON allowlist) and unconditionally inserts an entry into the shared `savedCallbacks` map before any allowlisting/rate-limiting is applied. `pruneCallbacks` evicts the oldest entries by `createdAt` once the map exceeds `MaxSavedCallbacks`, so an attacker who floods unique, never-answered `MessageId`s can force early eviction of older, legitimate, still-pending callbacks, denying those users their trigger response.

### Finding Description
`HandleLegacyUserMessage` validates message shape/signature and payload freshness, then does: [1](#0-0) 
storing every syntactically valid request into `h.savedCallbacks` keyed by `MessageId`, with an explicit code comment acknowledging no allowlist/rate-limit is applied at this point: [2](#0-1) 

The only gate before reaching this line is `Message.Validate()`, which requires a well-formed ECDSA signature recoverable to *some* address — it does not check that address against a DON-specific allowlist: [3](#0-2) 
Any holder of an ECDSA keypair (a "valid gateway signing key" per the precondition, but not necessarily authorized for the target workflow/DON) can therefore mint unlimited unique `MessageId`s and get each one persisted in `savedCallbacks`.

`pruneCallbacks` runs periodically (`CallbackPruneIntervalSec`, default 30s) and only bounds the map size after the fact: [4](#0-3) 
It first deletes entries older than `CallbackMaxAgeSec` (default 120s), then, if still above `MaxSavedCallbacks` (default 20000), sorts remaining entries by `createdAt` and evicts down to half of `MaxSavedCallbacks` — i.e., oldest-first, irrespective of whether an entry is still within its age budget or "legitimate."

Because insertion is unauthenticated-by-allowlist and unrate-limited at this stage, and eviction is FIFO-by-creation-time once the cap is hit, an attacker can:
1. Continuously submit unique, valid-signature `triggerRequest`s (never returning any DON response) at a rate exceeding `pruneCallbacks`'s clean-up rate.
2. Push `len(savedCallbacks)` above `MaxSavedCallbacks` between prune cycles.
3. Cause the next `pruneCallbacks()` run to evict the oldest half of entries by `createdAt` — which will include a legitimate victim's callback if it was inserted before the attacker's flood, even though it has not yet expired by age and its DON nodes have not yet responded.
4. The victim's `handleWebAPITriggerMessage` will find no entry for their `MessageId` (deleted) once the real DON response arrives, so the response is silently dropped and their `callback.Wait(ctx)` in `gateway.ProcessRequest` will time out. [5](#0-4) [6](#0-5) 

The existing `nodeRateLimiter` only throttles DON-node-originated outgoing HTTP requests (`handleWebAPIOutgoingMessage`), not the incoming user-message insertion path, so it provides no protection here: [7](#0-6) 
The test suite explicitly flags this gap with a TODO: [8](#0-7) 

### Impact Explanation
This is a denial-of-service against legitimate users' pipeline-trigger responses: a flood of cheap, signed-but-never-answered requests can cause the gateway to evict a legitimate, still-pending callback before its DON response arrives, silently dropping that user's trigger result (they observe a request timeout instead of their expected response). It also grows `savedCallbacks` memory usage up to `MaxSavedCallbacks` entries continuously. This matches a "denial of service / resource exhaustion affecting availability of legitimate requests" class impact, scoped to this handler's request/response delivery — not a fund-loss or cross-user data-disclosure bug.

### Likelihood Explanation
Any holder of a signing keypair that can construct a syntactically valid `api.Message` (per `Message.Validate()`, no DON-specific allowlist check gates insertion into `savedCallbacks`) can trigger this. The attack is trivial to script: generate N ECDSA keys or reuse one key with N unique `MessageId`s, sign N `triggerRequest`s with fresh timestamps, and never respond as a DON node. Feasibility depends on being able to reach the gateway's user-message endpoint and exceed the 30-second prune interval throughput with more than `MaxSavedCallbacks` (20000 default) concurrent unanswered entries — a non-trivial but plausible volume for a scripted flood, especially since no per-sender rate limit currently exists on this insertion path.

### Recommendation
- Apply per-sender rate limiting and/or a DON-specific allowlist check on `HandleLegacyUserMessage` before inserting into `savedCallbacks` (the code already has a `// TODO: apply allowlist and rate-limiting here` marker at this exact point).
- Consider bounding `savedCallbacks` per-sender (e.g., cap outstanding pending callbacks per signer address) rather than only a global FIFO cap, so one attacker's flood cannot evict other senders' entries.
- Alternatively/in addition, reject new insertions once at `MaxSavedCallbacks` capacity (backpressure) rather than silently evicting older legitimate entries.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with a small `MaxSavedCallbacks` (e.g., 10) and `CallbackMaxAgeSec` large enough that age-based expiry doesn't trigger.
2. Insert one legitimate callback via `HandleLegacyUserMessage` for `victimMsg` (unique `MessageId`, valid signature) and record its callback channel.
3. Flood the handler with `HandleLegacyUserMessage` calls for N > `MaxSavedCallbacks` unique attacker `MessageId`s (all validly signed, distinct keys/ids), never sending any `HandleNodeMessage` response for them.
4. Call `handler.pruneCallbacks()` directly (or wait for the ticker).
5. Assert: `handler.savedCallbacks` no longer contains the victim's `MessageId` (demonstrating premature eviction), and/or that calling `HandleNodeMessage` with the victim's real DON response afterward returns without invoking `savedCb.SendResponse`, leaving `victimCallback.Wait(ctx)` to time out.
6. Contrast with a control run where the flood volume stays under `MaxSavedCallbacks`, showing the victim's entry survives — confirming the eviction is purely capacity/order-driven and attacker-triggerable.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-334)
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

**File:** core/services/gateway/api/message.go (L54-88)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
