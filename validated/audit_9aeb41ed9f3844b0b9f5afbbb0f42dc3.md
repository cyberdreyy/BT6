## Answer

The `MessageId` (`message_id` field) that keys the `savedCallbacks` map in the legacy gateway handler is fully attacker/client-controlled — it is bounded only to ≤128 bytes and "not ending in a null char" by `Message.Validate()`, with no uniqueness enforcement at the transport layer [1](#0-0) . `HandleLegacyUserMessage` stores the caller's callback in the shared `h.savedCallbacks` map keyed solely by this attacker-supplied `MessageId`, **without checking whether an entry already exists**, unconditionally overwriting whatever was there before [2](#0-1) . This is unlike the newer v2 trigger handler, which explicitly detects and rejects a duplicate/in-flight `requestID` with `jsonrpc.ErrConflict` before registering a callback [3](#0-2) , and unlike `RequestCache.NewRequest`, which also rejects on collision ("request already exists") [4](#0-3) .

When any DON node later responds with that `MessageId`, `handleWebAPITriggerMessage` looks the ID up, deletes it, and delivers the response to whichever callback is currently stored there — with no sender/requester validation, "first response wins" — before returning [5](#0-4) .

This maps to the same bug class as the external report: an unprivileged actor manipulates unguarded, race-prone shared state (there, a bare `try{}catch{}` leaving global draw state stuck; here, an unchecked map write on a client-controlled key) to disrupt or hijack another party's in-flight request. Here the concrete, higher-severity variant is response hijacking/DoS: an attacker who learns or predicts a victim's `MessageId` (e.g., by observing pending requests, or simply racing a chosen ID before the victim submits their own) can call `HandleLegacyUserMessage` with the same ID, overwrite the victim's registered callback, and receive the victim's node response, while the victim's original callback is silently orphaned until pruning (up to `CallbackMaxAgeSec`, default 120s) — a cross-user response confusion condition [6](#0-5) .

I was not able to fully trace the outer HTTP path that ultimately invokes `HandleLegacyUserMessage` (e.g., which webhook/gateway ingress endpoint sets `MessageId` from client input versus generating it server-side) within the available index; this detail matters for the practical severity/likelihood assessment and is worth explicit verification by a Devin session with full repo access, since size limits on the index may have excluded the relevant ingress controller code.

### Title
Legacy gateway handler overwrites in-flight user callbacks on attacker-chosen MessageId, allowing cross-user response hijacking - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`HandleLegacyUserMessage` registers a caller-supplied callback in a shared map keyed by the attacker-controlled `MessageId` field without checking for an existing in-flight entry, unlike its own newer v2 sibling and the `RequestCache` utility, both of which reject duplicate IDs.

### Finding Description
`Message.Validate()` only enforces length/format constraints on `MessageId`, not uniqueness [1](#0-0) . `HandleLegacyUserMessage` then does:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}
```
overwriting any existing entry for that ID [2](#0-1) . When a node later replies with that `MessageId`, `handleWebAPITriggerMessage` pulls whatever callback is currently registered and delivers the response to it, deleting the entry [5](#0-4) . There is no association between the callback and the original request's sender/session beyond the raw string key, so a second unprivileged request using the same `MessageId` silently replaces the first requester's pending callback registration.

### Impact Explanation
An unprivileged client can cause: (1) denial of service against another user's pending trigger request — their callback is orphaned and only resolves via timeout-based pruning (`CallbackMaxAgeSec`, default 120s) [7](#0-6) ; and (2) response interception — the attacker's callback receives the node's response payload intended for the original request. This is analogous to the reported bug class in that an unprivileged actor manipulates unguarded shared mutable state tied to a request lifecycle to disrupt or misdirect legitimate protocol flow.

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or observe a victim's `MessageId` before the node's response is delivered — feasible if IDs are client-generated/predictable or observable via any monitoring/logging surface, but this could not be fully confirmed from the available index since the outer HTTP ingress path that constructs the `Message` from a user webhook was not fully traceable here.

### Recommendation
Add the same duplicate-ID rejection semantics already present in `RequestCache.NewRequest` and the v2 `setupCallback` (check-then-insert under lock, return `jsonrpc.ErrConflict`/error instead of overwriting) to `HandleLegacyUserMessage`, and consider binding the callback map key to `(Sender, MessageId)` rather than `MessageId` alone.

### Proof of Concept
1. Attacker sends a legacy trigger message with `MessageId = "X"` and a callback that immediately captures whatever is returned.
2. Victim independently (or slightly earlier) sends a legitimate trigger message that also uses (or is made to collide with) `MessageId = "X"`.
3. `HandleLegacyUserMessage` overwrites `h.savedCallbacks["X"]` with the attacker's callback [2](#0-1) .
4. When the DON node responds for `MessageId = "X"`, `handleWebAPITriggerMessage` delivers the payload to the attacker's callback, and the victim's original request is orphaned until it times out [5](#0-4) .

### Citations

**File:** core/services/gateway/api/message.go (L54-67)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
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
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
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
