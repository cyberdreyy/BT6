### Title
Cross-user response hijack via MessageId collision in gateway capabilities handler's savedCallbacks map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.savedCallbacks` map used to correlate a DON node's async trigger response with the originating user callback is keyed solely by the client-supplied `MessageId` string, with no binding to the requester's identity/signer. Because `MessageId` is fully attacker-chosen (it becomes the JSON-RPC request `id`), an attacker can submit a request whose `MessageId` collides with (or is reused after) another user's entry, causing a late/duplicate DON node response for the original request to be delivered to the attacker's callback instead of (or in addition to) the legitimate caller's.

### Finding Description
`HandleLegacyUserMessage` stores the caller's `handlers.Callback` in a global map keyed by `msg.Body.MessageId`: [1](#0-0) 

`MessageId` originates directly from the JSON-RPC request `id` field supplied by the caller, with no server-side uniqueness enforcement — `ValidatedMessageFromReq` simply copies `req.ID` into `m.Body.MessageId`: [2](#0-1) 

`Message.Validate()` only checks length/format constraints on `MessageId` (max 128 bytes, no trailing null), never uniqueness or ownership binding: [3](#0-2) 

When a DON node later responds, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId` and delivers the payload to whatever callback is currently stored under that key, with no check that the response corresponds to the same requester/signer that submitted the original message: [4](#0-3) 

Entries are only removed by (a) first-response consumption, (b) age-based pruning in `pruneCallbacks` using `CallbackMaxAgeSec`, or (c) capacity-based eviction: [5](#0-4) 

Because the request is broadcast to all DON members and only the first node response is consumed (subsequent responses for the same ID simply find nothing and are dropped), there is a window after the first response (or after pruning/eviction) during which the `MessageId` key is free. If an attacker submits a new request reusing that same `MessageId` in this window, their callback is stored under the key. Any subsequent/late node response bearing that same `MessageId` (e.g., a slow DON member's duplicate response to the *original* victim message, or the DON reprocessing a stale message) will now match the attacker's freshly inserted `savedCallback` and be delivered to the attacker instead of being silently dropped as intended — i.e., cross-user response confusion. The signature check that occurs in `HandleNodeMessage` (`msg.Body.Sender != nodeAddr`) verifies the *node* identity, not the identity of the original requesting user, so it does nothing to prevent this.

This is not merely a theoretical race: the reference invocation script for this API defaults its `MessageId` to a hardcoded/predictable value (`"12345"`), illustrating that predictable/non-random IDs are a realistic client pattern, increasing the practical likelihood of collision: [6](#0-5) 

### Impact Explanation
An unprivileged, unauthenticated party who can submit signed gateway messages with an arbitrary self-chosen `MessageId` can cause another user's DON-node response (which may contain outbound HTTP fetch results processed on the victim's behalf, per `handleWebAPIOutgoingMessage`) to be delivered into the attacker's own callback instead of the victim's, or cause the victim's legitimate response to be silently dropped. This is a cross-user response confusion / hijack, matching the "unauthorized action on another user's request/response" bounty class.

### Likelihood Explanation
Exploitation requires only the ability to sign and submit a gateway JSON-RPC message with an attacker-chosen `id`/`MessageId` — any party capable of generating an ECDSA keypair can do this; no special role or credential is needed. The attacker needs to guess or predict a `MessageId` in use (feasible when clients use short/predictable/sequential IDs, as the shipped sample script itself does) and time submission to land inside the (short but non-zero) window after the original entry is removed (consumed, expired via `CallbackMaxAgeSec`, or evicted) but before a late/duplicate node response arrives. This is a timing-dependent race rather than deterministic, which lowers — but does not eliminate — practical exploitability.

### Recommendation
Bind `savedCallbacks` entries to the original requester's identity (e.g., include the validated signer address / a server-generated unpredictable correlation token in the map key, or store and re-verify the signer/sender on response delivery) rather than relying solely on the caller-supplied `MessageId`. Alternatively, generate the correlation key server-side (not attacker-controlled) and reject/ignore any node response whose associated original message signer does not match the stored request's signer.

### Proof of Concept
1. Unit/integration test in `core/services/gateway/handlers/capabilities/handler_test.go`:
   - Caller A calls `HandleLegacyUserMessage` with `MessageId = "X"`, callback `cbA`.
   - Simulate first node response for "X" being consumed (`handleWebAPITriggerMessage`), which deletes the map entry after delivering to `cbA`.
   - Caller B (different signer) calls `HandleLegacyUserMessage` reusing `MessageId = "X"`, callback `cbB`.
   - Simulate a second/late node response bearing `MessageId = "X"` (representing a slow DON member's duplicate reply meant for A's original request).
   - Assert that this late response is delivered to `cbB` (attacker) — demonstrating cross-user hijack — instead of being dropped, and that `cbA`'s wait channel never receives this second payload.
2. A second variant should force pruning (set `CallbackMaxAgeSec` very low, call `pruneCallbacks()` to remove A's entry) and then have B reuse `MessageId = "X"`, followed by a late node response for "X", asserting the same hijack outcome.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/common/message_util.go (L46-57)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
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

**File:** core/scripts/gateway/web_api_trigger/invoke_trigger.go (L55-57)
```go
	privateKey := flag.String("private_key", "65456ffb8af4a2b93959256a8e04f6f2fe0943579fb3c9c3350593aabb89023f", "Private key to sign the message with")
	messageID := flag.String("id", "12345", "Request ID")
	methodName := flag.String("method", "web_api_trigger", "Method name")
```
