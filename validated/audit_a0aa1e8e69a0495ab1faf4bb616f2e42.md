### Title
Cross-user response confusion via colliding client-chosen `MessageId` in gateway trigger callback map - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.savedCallbacks` map used to route a DON node's `web_api_trigger` response back to the originating HTTP caller is keyed only by `msg.Body.MessageId`, a value fully chosen by the requesting client and only length/format validated, not checked for collisions with in-flight requests, and not scoped by `Sender`. Two different signed callers targeting the same DON can choose the same `MessageId`, causing the second caller's registration to silently overwrite the first's, so whichever node response resolves first for that ID is delivered to the caller currently holding the map slot rather than the caller who actually originated that response.

### Finding Description
`HandleLegacyUserMessage` unconditionally stores the caller's callback under the client-supplied ID with no existence check: [1](#0-0) 

`msg.Body.MessageId` is taken directly from the incoming JSON-RPC request ID and only validated for length/null-suffix — never for uniqueness or binding to `Sender`: [2](#0-1) [3](#0-2) 

The only per-request guard in the gateway's HTTP entrypoint is a max-length check on the ID, not a uniqueness check: [4](#0-3) 

When a node later responds, `handleWebAPITriggerMessage` looks up and deletes `savedCallbacks[msg.Body.MessageId]` and delivers the response to whatever `savedCallback` currently occupies that slot — there is no check that the responding node's message actually corresponds to the request that registered that callback (no Sender/DonId component in the key, and DonId is implicit-only via handler instance): [5](#0-4) 

Exploit flow: user A submits a request with `MessageId = "X"` to the DON; `HandleLegacyUserMessage` stores A's callback at `savedCallbacks["X"]` and forwards A's message to all DON members. Before any node responds to A, user B — also an unprivileged but validly-signed caller — submits their own request choosing the same `MessageId = "X"`; this overwrites `savedCallbacks["X"]` with B's callback, silently orphaning A's entry (A's HTTP call now hangs until the gateway's own `callback.Wait(ctx)` timeout). Since both A's and B's requests were forwarded to the DON nodes under the same ID, whichever underlying node response resolves the ID `"X"` first (which may be a response actually correlated with A's original request) is deleted from the map and delivered to the callback object currently stored there — B's. B thus can receive data destined for A's session/callback. No signature, sender, or DonId partitioning check exists to prevent this collision at the gateway's user-callback layer.

### Impact Explanation
This is a cross-user response confusion within the Gateway DON handler: a low-privileged, unauthenticated-relative-to-node-trust user who can submit signed gateway requests can (a) cause a denial of service for another concurrent requester by orphaning their callback, and (b) under a race, receive a response that was intended for another user's request. This maps to the Chainlink bounty impact class of "unauthorized read of another user's data returned via gateway" / cross-tenant response leakage, plus a request-hijacking DoS component.

### Likelihood Explanation
Exploitability requires only: (1) ability to sign and submit `web_api_trigger` requests to the same gateway/DON (any credential holder able to send signed gateway messages, matching the allowed unprivileged attacker class), and (2) choosing a `MessageId` that collides with another in-flight request's chosen ID within the callback's TTL window (`defaultCallbackMaxAgeSec` = 120s). Since `MessageId` is entirely attacker-chosen (up to 128 bytes) and there is no uniqueness enforcement across senders, an attacker can either guess/brute-force a victim's ID space (if IDs are predictable, e.g., sequential or low-entropy client-generated) or, more reliably, deliberately race by submitting many requests with a spread of candidate/observed IDs to increase collision probability against a targeted victim's known request pattern. This is a real, reachable race condition, not merely theoretical, though its practical severity depends on how unpredictable legitimate callers' `MessageId`s are (if callers always use high-entropy UUIDs, collision requires either guessing or the attacker choosing to collide with a value it has learned, e.g., via shared/observable request IDs).

### Recommendation
Scope `savedCallbacks` keys by both `Sender` and `MessageId` (e.g., `sender+":"+messageId`) instead of `MessageId` alone, and reject/register-fail requests that collide with an existing unexpired entry for the same key rather than silently overwriting. On the node-response path in `handleWebAPITriggerMessage`, verify that the responding node's message correlates to the specific request it was sent for (e.g., include the expected sender/request context alongside the callback so a response can only be matched to the callback that actually issued that request to the nodes).

### Proof of Concept
Add a table/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Call `setupHandler(t)` to get `handler`, `don`, `nodes`.
2. Create callback A (`hc.NewCallback()`), call `handler.HandleLegacyUserMessage(ctx, msgA, callbackA)` with `msgA.Body.MessageId = "X"`, signed by node/sender A key, targeted method `MethodWebAPITrigger`.
3. Before resolving A, create callback B and call `handler.HandleLegacyUserMessage(ctx, msgB, callbackB)` with the same `MessageId = "X"` (different signer/sender), asserting `handler.savedCallbacks["X"]` now points to B's callback (overwrite confirmed) and that A's callback object is no longer reachable via the map.
4. Simulate a node response correlated to A's original outbound message (construct `resp` via `hc.ValidatedResponseFromMessage` using `msgA`'s ID) and call `handler.HandleNodeMessage(ctx, resp, nodeAddr)`.
5. Assert that `callbackB.Wait(ctx)` receives the response payload derived from `msgA` (proving B received A's data) while `callbackA.Wait(ctx)` times out/never resolves — demonstrating cross-user response confusion and orphaned-callback DoS.

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

**File:** core/services/gateway/handlers/common/message_util.go (L46-52)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
```

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```
