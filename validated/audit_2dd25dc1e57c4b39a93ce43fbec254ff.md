### Title
Replay of a signed legacy WebAPI-trigger message causes duplicate capability dispatch to the DON - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`gateway.ProcessRequest` in `core/services/gateway/gateway.go` performs signature and staleness validation but never checks for message-ID uniqueness, and the WebAPI/legacy capabilities handler it delegates to (`handler.HandleLegacyUserMessage`) also performs no replay/duplicate detection. An attacker who captures one valid signed legacy request can resend the identical raw bytes any number of times within the allowed message-age window, and each resend is independently forwarded to every DON member node, causing the trigger to be dispatched (and potentially executed) multiple times.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes the request, calls `msg.Validate()` (which only checks signature format/length and field constraints, `core/services/gateway/api/message.go:54-88`), resolves the handler by `DonId`, and calls `h.HandleLegacyUserMessage(ctx, msg, callback)` [1](#0-0) . Neither `Message.Validate` nor `ProcessRequest` tracks previously seen `MessageId`/`Signature` pairs.

For the WebAPI capabilities handler, `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`) only rejects messages that are *too old* via `payload.Timestamp` vs `MaxAllowedMessageAgeSec` [2](#0-1) ; it does not reject messages it has already processed. It then unconditionally stores the callback keyed by `msg.Body.MessageId` and forwards the request to every DON member: [3](#0-2) 
Each call to `HandleLegacyUserMessage` with the same signed bytes overwrites `h.savedCallbacks[msg.Body.MessageId]` and calls `don.SendToNode` again for every member — there is no "request already exists" guard like the one implemented in the shared `RequestCache` used by other handlers (`core/services/gateway/handlers/common/requestcache.go:50-63`, which explicitly returns `"request already exists"` for a duplicate `{sender, MessageId}` key). Because the capabilities/WebAPI handler does not use `RequestCache`, any number of identical replays within the staleness window are individually forwarded to the DON, resulting in duplicate trigger dispatch attributable to the original signer.

### Impact Explanation
This allows an unprivileged attacker who observed one valid signed legacy gateway message (e.g., via a public gateway echo or network capture) to cause repeated forwarding of the same trigger request to all DON nodes, leading to duplicate capability invocation and DON/gateway resource exhaustion attributed to the victim signer — this falls under the "duplicate job run / unauthorized triggered execution" and "resource exhaustion" bounty impact classes rather than fund loss or key disclosure.

### Likelihood Explanation
Exploitability requires only passive observation of one valid signed request (no private key or credentials needed) and resending the exact raw bytes, which is straightforward and fully repeatable for the duration of `MaxAllowedMessageAgeSec` (default staleness window). No operator, node, or admin access is needed, matching the "unprivileged external attacker" threat model.

### Recommendation
Add message-ID/signature-based replay protection at the handler level (or centrally in `gateway.ProcessRequest`) analogous to the existing `common.RequestCache.NewRequest` duplicate-key rejection, so that a repeated `(Sender, MessageId)` (or `(Signature)`) is rejected with an error instead of being re-forwarded to the DON. Apply this consistently to `capabilities.handler.HandleLegacyUserMessage` and `handlers.dummyHandler.HandleLegacyUserMessage`, which currently unconditionally overwrite `savedCallbacks` and re-send.

### Proof of Concept
Extend `core/services/gateway/gateway_test.go` (or add a handler-level test in `core/services/gateway/handlers/capabilities`):
1. Build a signed legacy WebAPI-trigger message via `newSignedLegacyRequest`/`webapicap.TriggerRequestPayload` with a valid, non-stale `Timestamp`.
2. Call `gw.ProcessRequest(ctx, req, "")` twice (or N times concurrently) with the identical raw bytes.
3. Assert on the mock `DON.SendToNode` (or `don` mock) that `SendToNode` was invoked once per DON member **per replay**, i.e., `N * len(members)` total calls, proving duplicate dispatch.
4. Contrast with a handler using `common.RequestCache` (e.g., functions/vault handler) where the second `NewRequest` call for the same `{sender, MessageId}` returns `"request already exists"` and no duplicate forwarding occurs — showing the capabilities/WebAPI handler lacks the equivalent guard.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
```go
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
