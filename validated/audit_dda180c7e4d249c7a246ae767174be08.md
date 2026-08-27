### Title
Cross-user response confusion via unauthenticated, client-controlled `message_id` collision in Gateway WebAPI trigger handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The Gateway's legacy WebAPI trigger flow correlates a user's inbound request with the eventual DON node's response using only `msg.Body.MessageId`, a field fully controlled by the unauthenticated/unprivileged caller of the gateway's HTTP endpoint. There is no check for whether a callback is already registered under that ID and no binding of the stored callback to any request-specific secret, sender identity, or nonce. This mirrors the audit's `[M-02]` root cause of "confirm state using only an external identifier without validating it corresponds to the original request context" — instead of `chain_hash` vs `request.chain`, here it is `MessageId` uniqueness/ownership that is never verified.

### Finding Description
`HandleLegacyUserMessage` stores the caller's callback keyed solely by the caller-supplied `MessageId`: [1](#0-0) 

`MessageId` validation only checks length and a null-byte suffix — it imposes no uniqueness, unpredictability, or binding to the calling client: [2](#0-1) 

When a DON node later returns a `web_api_trigger` response, the handler looks up the stored callback purely by `MessageId` and delivers the DON's response to whichever callback is currently registered under that ID: [3](#0-2) 

If two callers (e.g., a legitimate user and a malicious/unprivileged caller reachable via the internet-facing gateway) submit requests with the same `MessageId` to the same DON handler, the second `HandleLegacyUserMessage` call silently overwrites the first entry in `savedCallbacks`: [1](#0-0) 

The original caller's request is still forwarded to DON nodes and a legitimate response is later produced, but that response is delivered to the *attacker's* callback (the last writer wins), not to the original requester — exactly analogous to the reported bug class where an event is confirmed against the wrong originating context because only a shared/non-unique correlator was checked.

### Impact Explanation
This allows response hijacking / cross-user response confusion at the gateway boundary: an unprivileged actor able to submit gateway user messages (the same class of caller the audited "unpprivileged withdrawal confirmer" represents) can intercept a response meant for another user's WebAPI trigger request by guessing or colliding on `MessageId`. Depending on what the WebAPI trigger payload carries back to the user (workflow-triggered data, potentially sensitive computed results), this could leak data intended for a different requester, or cause the legitimate requester's channel to hang/timeout (denial of a specific request) while the attacker silently consumes the answer.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires the attacker to know or guess an in-flight `MessageId` (up to 128 chars, no format constraint enforced beyond length/null-suffix) within the roughly 2-minute callback TTL window (`defaultCallbackMaxAgeSec = 120`), and to target the same DON/handler as the victim: [4](#0-3) 
If `MessageId`s are predictable (sequential counters, timestamps, or client-chosen non-random strings by design/integration), the collision becomes trivial to force deliberately.

### Recommendation
Bind the stored callback to more than the bare `MessageId`: e.g., reject registration if an entry for that `MessageId` already exists and is not expired, and/or derive/validate the ID against a per-request, server-generated nonce or hash of the caller's own signed request context, similar to how the audit's fix compared `chain_transaction.chain_id` against `request.chain`. At minimum, `HandleLegacyUserMessage` should return an error (rather than silently overwrite) when `h.savedCallbacks[msg.Body.MessageId]` is already populated.

### Proof of Concept
1. Attacker (unprivileged, internet-facing) sends a JSON-RPC request to the Gateway with `method = web_api_trigger` and `id = "X"` (a `MessageId` chosen to match/guess a victim's in-flight request), routed to `HandleLegacyUserMessage`.
2. Simultaneously, a legitimate user's earlier request with the same `MessageId = "X"` is still pending in `h.savedCallbacks["X"]`, waiting on a DON response.
3. The attacker's call to `HandleLegacyUserMessage` overwrites `h.savedCallbacks["X"]` with the attacker's callback (`core/services/gateway/handlers/capabilities/handler.go:411-414`).
4. When the DON node responds with `MethodWebAPITrigger` and `MessageId = "X"`, `handleWebAPITriggerMessage` fetches `h.savedCallbacks["X"]` (now the attacker's) and delivers the response to the attacker instead of the original requester (`core/services/gateway/handlers/capabilities/handler.go:148-162`). [3](#0-2) [1](#0-0)

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
