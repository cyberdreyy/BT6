Confirmed: `Message.Body.MessageId` is fully attacker-chosen (any string up to 128 chars, only constrained by not ending in a null byte), and there is no server-side enforcement of global uniqueness beyond the per-handler `savedCallbacks` map key itself.### Title
Cross-user response confusion via colliding attacker-chosen `MessageId` overwriting `savedCallbacks` entries - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each pending request's callback in `h.savedCallbacks[msg.Body.MessageId]` without checking whether that key already holds another (victim) requester's callback, and `MessageId` is a fully attacker-chosen, signature-covered field with no global uniqueness enforcement. By submitting a request with a `MessageId` colliding with a victim's in-flight request, an attacker can overwrite the map entry so that whichever DON node response for that ID arrives first is delivered to the attacker's own HTTP connection — not necessarily the response to the attacker's own submitted payload.

### Finding Description
- `gateway.ProcessRequest` (`core/services/gateway/gateway.go:264-291`) creates a fresh `handlerscommon.Callback` per HTTP request and calls `h.HandleLegacyUserMessage(ctx, msg, callback)`, then blocks on `callback.Wait(ctx)`, returning `response.RawResponse` and `api.ToHttpErrorCode(response.ErrorCode)` (or `api.RequestTimeoutError` → HTTP 504 on `ctx` timeout) directly to the caller's HTTP response. [1](#0-0) 
- `handler.HandleLegacyUserMessage` unconditionally overwrites the map entry keyed only by the client-supplied `msg.Body.MessageId`: [2](#0-1) 
- `MessageId` is fully attacker-controlled (any non-null-terminated string ≤128 bytes) and only validated for length/format, not uniqueness, in `Message.Validate()`: [3](#0-2) 
- When a node response for that `MessageId` arrives, `handleWebAPITriggerMessage` looks the ID up in `savedCallbacks`, deletes it, and — if found — delivers the response to whichever callback currently occupies that slot: [4](#0-3) 

Exploit flow: Victim submits a legacy request with `MessageId = X`; handler registers `savedCallbacks[X] = victimCallback` and forwards the message to all DON members. Before the DON responds, attacker submits their own signed request also using `MessageId = X` (attacker controls this field and can guess/observe it via metadata, logs, or an "own colliding" test if IDs are session-derived/predictable); handler overwrites `savedCallbacks[X] = attackerCallback`, forwarding attacker's own message body to the DON as well. Whichever DON response for ID `X` is received first by the gateway (which could be the node's answer to the *victim's* original payload, since both are outstanding under the same ID) is routed to `savedCallbacks[X]`, which now holds `attackerCallback`, and that raw response (and its `ErrorCode`) is returned to the attacker's HTTP connection via `gateway.ProcessRequest`. The victim's original callback reference is orphaned in the map, so the victim's `callback.Wait(ctx)` will eventually time out, producing `api.RequestTimeoutError`/HTTP 504 for the victim regardless of the true outcome — this asymmetry (attacker gets a real/early answer, victim gets a forced timeout) is itself an observable side-channel confirming the collision succeeded, in addition to the more severe risk of the attacker directly receiving the victim's actual response payload rather than merely a status-code oracle. No authentication/authorization check, presenter redaction, or per-sender key namespacing stops this because the map key is process-wide and derived solely from the unauthenticated, attacker-suppliable `MessageId` field.

### Impact Explanation
This is cross-user response confusion / disclosure of another requester's response data and forced denial-of-service (timeout) of the victim's original request, matching Chainlink's "unauthorized access to another user's data" / "session hijacking–like" impact class rather than a mere side channel — the attacker can end up receiving the actual DON response body computed for the victim, and can reliably force the victim's request to appear as `api.RequestTimeoutError` (HTTP 504) by winning the map-overwrite race.

### Likelihood Explanation
Requires only being able to submit a signed legacy gateway request with an attacker-chosen `MessageId` (no privileged role needed — any signer can call the legacy API), and needing to guess or learn a victim's in-flight `MessageId`. If `MessageId`s are predictable, short-lived, or observable (e.g., sequential, echoed in logs/metrics, or otherwise inferable), this is straightforward and repeatable; if the wider system always uses high-entropy random IDs, the practical likelihood is lower but the code path itself provides zero defense-in-depth against the collision.

### Recommendation
In `handler.HandleLegacyUserMessage`, reject the request (or generate a fresh internally-managed ID) if `h.savedCallbacks[msg.Body.MessageId]` already exists, mirroring the existing "request ID already exists" check used in `confidentialrelay/handler.go` and `vault/handler.go`'s `newActiveRequest`. Additionally, scope `savedCallbacks` keys by sender address (or a server-generated correlation ID) so that the same `MessageId` cannot collide across different requesters.

### Proof of Concept
Go handler-level integration test plan (in `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Create the handler with a mock `DON`.
2. Victim: call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` where `victimMsg.Body.MessageId = "collide-id"`.
3. Before responding, attacker: call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` with the same `MessageId = "collide-id"` (different `Sender`, different `Payload`).
4. Assert `h.savedCallbacks["collide-id"]` now points to `attackerCallback` (overwrite confirmed) — i.e., `victimCallback` is unreachable via the map.
5. Simulate the DON node responding to the *victim's* forwarded message (e.g., an `api.Message` with `MessageId = "collide-id"` and a payload representing the victim's private result) via `handler.HandleNodeMessage`/`handleWebAPITriggerMessage`.
6. Assert that `attackerCallback.Wait(ctx)` receives this response (containing the victim's data) instead of `victimCallback.Wait(ctx)`.
7. Assert `victimCallback.Wait(ctx)` times out and returns `api.RequestTimeoutError` (mapped to HTTP 504 via `api.ToHttpErrorCode`), demonstrating the observable timeout side-channel/oracle and the underlying data-confusion vulnerability.

### Citations

**File:** core/services/gateway/gateway.go (L264-291)
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
	g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.ErrorCode.String(), duration)
	g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.ErrorCode.String())

	g.lggr.Debugw("received response from handler", "handler", handlerKey, "response", response, "requestID", jsonRequest.ID)
	promRequest.WithLabelValues(response.ErrorCode.String()).Inc()
	return response.RawResponse, api.ToHttpErrorCode(response.ErrorCode)
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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
