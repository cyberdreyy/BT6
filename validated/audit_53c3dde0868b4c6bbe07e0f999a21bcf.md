### Title
Unvalidated, attacker-controlled `MessageId` allows cross-user response hijacking in the WebAPI gateway handler - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The Opyn `_redeem` bug stemmed from trusting an attacker-supplied identifier (a self-deployed oToken address) as if it referred to a legitimate, platform-registered asset, with no cross-check against the real registry. The same root-cause pattern — accepting a caller-supplied identifier at face value and using it to key a sensitive lookup/state mutation without validating its legitimacy or ownership — is reachable in the Chainlink gateway's WebAPI capability handler via the `MessageId` field, which is part of the client-supplied message body and is used unchecked as the sole key into the in-memory `savedCallbacks` map.

### Finding Description
`HandleLegacyUserMessage` accepts an inbound `api.Message` from an (unprivileged, internet-facing) gateway user and stores the caller's response `Callback` in a shared map keyed purely by the caller-supplied `msg.Body.MessageId`, with no check that this ID is unique, unpredictable, or bound to the requesting session: [1](#0-0) 

There is no verification prior to this write that `msg.Body.MessageId` is not already in use by another in-flight request. Later, when any DON node returns a response for that same `MessageId`, `handleWebAPITriggerMessage` looks the ID up in `savedCallbacks`, deletes it, and forwards the raw node response to whichever callback is currently associated with that ID: [2](#0-1) 

Because the map is keyed solely by an unvalidated, externally supplied string (analogous to the unvalidated oToken address in the GammaProtocol bug), a second, unprivileged user who submits a request with the same `MessageId` as a victim's in-flight request will overwrite the victim's map entry. Whichever request is currently registered under that ID receives the eventual node response — meaning an attacker can potentially receive a response destined for another user's request, or clobber/redirect a victim's pending callback, purely by supplying a colliding client-chosen identifier. This mirrors the report's core lesson: state was mutated/returned based on an identifier the caller supplied, without confirming it genuinely and exclusively corresponds to the caller's own request.

### Impact Explanation
This does not move funds directly, but it enables cross-user response confusion at the internet-facing gateway: an unprivileged client can, by choosing a colliding `MessageId`, hijack or corrupt delivery of another user's WebAPI trigger response, causing a user to receive attacker-influenced data or a legitimate user's response to be lost/misdelivered. Depending on what data flows through `MethodWebAPITrigger` responses (workflow trigger payloads), this could leak information intended for another user or disrupt correctness of workflow execution results.

### Likelihood Explanation
Reachability requires only that the attacker be able to send a message to the gateway's legacy user-message endpoint with a `MessageId` matching (or racing) a victim's concurrent request. The severity depends on how predictable/guessable legitimate `MessageId`s are and how much request concurrency exists; I was not able to fully verify, within the available index, whether an upstream layer (before reaching `HandleLegacyUserMessage`) enforces `MessageId` uniqueness or unpredictability (e.g., server-side ID generation) — this is a gap in my investigation and should be confirmed by reviewing the callers of `HandleLegacyUserMessage` and the `api.Message`/`MessageId` construction path end-to-end.

### Recommendation
- Reject or return an explicit conflict error when `msg.Body.MessageId` already exists in `savedCallbacks` instead of silently overwriting it (mirroring the existing `setupCallback`/`ErrConflict` protection already implemented for the newer `httpTriggerHandler.setupCallback`, see `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go` lines 398-406, which does perform this check).
- Ensure `MessageId` values used for legacy WebAPI trigger callbacks are either server-generated or bound to an authenticated session/requester, not purely client-controlled, before being used as a shared-state lookup key.

### Proof of Concept
Conceptual (not executed, due to lack of terminal access):
1. User A sends a legacy WebAPI trigger message with `MessageId = "X"`; `HandleLegacyUserMessage` stores A's callback under key `"X"` and fans the request out to DON nodes.
2. Before a node responds to A's request, Attacker B sends their own legacy WebAPI trigger message also using `MessageId = "X"`. This silently overwrites the map entry so key `"X"` now points to B's callback (`core/services/gateway/handlers/capabilities/handler.go` lines 411-420).
3. When a DON node responds with `MessageId = "X"` (intended for A), `handleWebAPITriggerMessage` looks up `"X"` and delivers the response to whichever callback is currently registered — now B's — resulting in B receiving A's response (or vice versa depending on timing), i.e., cross-user response confusion (`core/services/gateway/handlers/capabilities/handler.go` lines 148-162).

Note: I was unable to confirm within the indexed code whether callers of `HandleLegacyUserMessage` pre-validate `MessageId` uniqueness/unpredictability elsewhere in the request pipeline; if such validation exists upstream, this finding's likelihood would be substantially reduced. A full Devin session with repository access would be needed to trace the complete caller chain and confirm exploitability end-to-end.

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
