### Title
Cross-user response confusion via colliding `MessageId` overwriting `savedCallbacks` entries - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores the caller's `handlers.Callback` in `h.savedCallbacks` keyed solely by `msg.Body.MessageId`, a client-controlled string that `Message.Validate()` only bounds-checks for length (≤128) and a trailing NUL, with no per-sender/tenant uniqueness enforcement. Because the write at registration time (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) unconditionally overwrites any existing entry, two concurrent requesters who choose the same `MessageId` can cause `handleWebAPITriggerMessage` to deliver one requester's node response to the other requester's callback.

### Finding Description
`Message.Validate()` enforces only length and NUL-suffix constraints on `MessageId`: [1](#0-0) 
There is no association between `MessageId` and the caller's identity/signer; `Sender` is a separate field extracted from the signature and is not incorporated into the `savedCallbacks` key.

`HandleLegacyUserMessage` registers the requester's callback keyed purely by `msg.Body.MessageId`, without checking for or rejecting an existing entry under that key: [2](#0-1) 

`handleWebAPITriggerMessage`, invoked when a node's response arrives, looks up and deletes the map entry solely by `msg.Body.MessageId` and immediately hands the response to whichever `savedCallback` object occupies that slot: [3](#0-2) 

Exploit flow: Requester A sends a `web_api_trigger` message with `MessageId="X"`; `HandleLegacyUserMessage` stores A's callback under key `"X"` and forwards A's request to all DON nodes. Before nodes respond, Requester B sends a different `web_api_trigger` message reusing `MessageId="X"`; this silently overwrites the map entry with B's callback (A's callback reference is now orphaned in the map). When a node responds to A's original request — the response message from the node echoes back `MessageId="X"` (nodes reuse `requestBody.MessageId` when building the response, see `sendResponse` construction in `trigger.go`) — `handleWebAPITriggerMessage` looks up key `"X"`, finds B's callback, deletes the entry, and calls `savedCb.SendResponse` with A's response payload. B receives A's response data; A's own request either receives no response (silently dropped, since the entry was already consumed) or later crosses with B's actual response under a second collision. No auth/signature check, rate limiter, or presenter step in this path validates that the responding node's `MessageId` traces back to the same original sender that registered the callback.

### Impact Explanation
This is a genuine cross-user response confusion: attacker-controlled response/job data intended for one requester's `web_api_trigger` invocation (e.g., HTTP response payload fetched via the workflow's HTTP capability) can be delivered to a different, unrelated requester by simply colliding on a client-supplied `MessageId`. This matches the "cross-user response confusion" impact class explicitly called out in the audit rules and can leak data returned by one user's workflow/job execution to another user's callback.

### Likelihood Explanation
The attacker only needs the ability to submit two `web_api_trigger` messages to the same DON/gateway handler instance with an identical `MessageId` string (max 128 chars, only a trailing-NUL restriction). No elevated privilege beyond being a legitimate/unauthenticated caller of the legacy user-message gateway path is required — an unprivileged or workflow-level client fully controls the `MessageId` field it signs and sends. The race window is bounded by DON response latency (up to the callback max-age of ~120s by default), which is easily won by timing two requests moments apart. The collision is deterministic given identical `MessageId` values and is trivially repeatable.

Note: I was not able to fully trace the exact unauthenticated/authenticated HTTP entry point that invokes `HandleLegacyUserMessage` in this snapshot of the repo (the calling code lives in `core/services/gateway/gateway.go` / `multihandler.go`, which were found but not read in depth in this session) — confirming whether any upstream auth/session binds `MessageId` to a specific caller before reaching this handler would refine the precondition, but the core map-key design gives no defense-in-depth regardless of caller identity/authentication level.

### Recommendation
Namespace `savedCallbacks` by a tuple that includes the caller's verified identity (e.g., `Sender` address extracted from the signature) and/or `DonId`/`Receiver`, not just the raw client-supplied `MessageId`. Additionally, reject registration (return an error to the new caller) if an entry already exists under the composed key instead of silently overwriting it, and verify that the node response's implied requester (via `Receiver`/original `Sender`) matches the previously stored request's Sender before invoking `SendResponse`.

### Proof of Concept
Go unit test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct two valid, differently-signed `web_api_trigger` `*api.Message`s, A and B, both with `Body.MessageId = "collide-id"` but different `Sender`/payload.
2. Create two distinct `hc.NewCallback()` instances, `cbA` and `cbB`.
3. Call `handler.HandleLegacyUserMessage(ctx, msgA, cbA)`, then `handler.HandleLegacyUserMessage(ctx, msgB, cbB)`.
4. Assert `len(handler.savedCallbacks) == 1` and that the stored entry's `Callback == cbB` (proving A's registration was silently clobbered) — expected/desired behavior would instead be an error on step 3's second call, or two independently addressable slots.
5. Simulate a node response for A's original request by calling `handler.HandleNodeMessage` with a `jsonrpc.Response` whose body echoes `MessageId = "collide-id"` and `Sender = nodeAddr`.
6. Assert that `cbB.Wait(ctx)` (not `cbA`) receives A's response payload, and `cbA.Wait(ctx)` times out/never resolves — demonstrating that B's callback received a response for a request it never issued, confirming cross-user response confusion.

### Citations

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
