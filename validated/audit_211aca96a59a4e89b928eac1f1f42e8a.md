This confirms the message signature only requires a self-generated ECDSA keypair (no registered/allowlisted key check) — anyone can sign a message and it will pass `Message.Validate()`, so an attacker can trivially mint unlimited distinct `MessageId`/sender combinations. Combined with the explicit `// TODO: apply allowlist and rate-limiting here` comment in `HandleLegacyUserMessage`, there is no per-sender quota preventing a flood.

### Title
Unbounded, ownership-agnostic eviction in `pruneCallbacks` allows unprivileged flood to silently drop victims' pending gateway callbacks - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.pruneCallbacks` evicts the oldest entries in the shared `h.savedCallbacks` map whenever its size exceeds `MaxSavedCallbacks`, with no notion of per-sender ownership or quota. Because `HandleLegacyUserMessage` accepts any signed message from any self-generated key with no allowlist or per-sender rate limit (`// TODO: apply allowlist and rate-limiting here`), an unauthenticated attacker can flood the gateway with distinct `MessageId`s to force eviction of other users' still-pending callbacks before the DON response arrives.

### Finding Description
`HandleLegacyUserMessage` [1](#0-0)  stores a `savedCallback` keyed by `msg.Body.MessageId` in the shared `h.savedCallbacks` map for every accepted trigger request, with no allowlist or rate-limit check (the TODO comment at line 384 confirms this is not yet implemented) [2](#0-1) . The only requirement to reach this code path is a validly self-signed `api.Message`; `Message.Validate()`/`ExtractSigner()` only checks that *some* valid ECDSA signature exists and derives `Sender` from it — it does not check the sender against any allowlist [3](#0-2) . This means an attacker can generate arbitrarily many keys and craft arbitrarily many distinct `MessageId`s cheaply.

`pruneCallbacks` runs periodically and, if `len(h.savedCallbacks) > MaxSavedCallbacks`, sorts *all* entries (regardless of who created them) by `createdAt` and deletes the oldest half, with no per-sender/ownership awareness: [4](#0-3) . If a victim's earlier legitimate trigger request is among the oldest entries at prune time, it gets deleted. When the victim's genuine DON response later arrives at `handleWebAPITriggerMessage`/`HandleNodeMessage`, the lookup in `h.savedCallbacks` misses (`found == false`), and the function silently returns `nil` with no response ever delivered to the victim's blocked `callback.Wait(ctx)` call [5](#0-4) . The victim's HTTP client-facing `Gateway.ProcessRequest` will simply hit its own timeout later and return a "handler timeout" error, appearing as an ordinary — but attacker-induced — request failure [6](#0-5) .

This is a cross-user isolation violation: attacker traffic on the same DON handler can affect the lifecycle/availability of another unrelated user's request.

### Impact Explanation
Concrete impact is denial of service for legitimate node/workflow users of the legacy web-API-trigger gateway path: their responses are silently dropped and they experience a full-timeout delay before failing, with no distinguishing error indicating they were evicted rather than genuinely timed out. This matches Chainlink's "Denial of Service" bounty impact class for gateway/DON request handling — it does not compromise keys, funds, or authorization, but does break the isolation invariant between unrelated user requests on a shared/critical infrastructure component.

### Likelihood Explanation
Feasibility is high: the attacker needs no credentials beyond the ability to sign a JSON-RPC legacy request with a self-generated key (unauthenticated relative to the DON/node identity) and send it to the gateway's public HTTP endpoint (`Gateway.ProcessRequest` → `HandleLegacyUserMessage`). No allowlist currently gates this path. Generating `MaxSavedCallbacks` (default 20000) distinct signed messages and bursting them is inexpensive and fully repeatable, especially since eviction trims to `maxSize/2`, making repeated floods effective at keeping the map saturated with attacker entries.

### Recommendation
- Add per-sender (or per-API-key) admission control/rate-limiting before inserting into `h.savedCallbacks`, as already flagged by the TODO at handler.go:384, so a single sender cannot occupy an unbounded share of the map.
- Change `pruneCallbacks` eviction policy to be fair across senders (e.g., cap entries per sender, or only evict entries belonging to senders that are over their own quota) rather than globally oldest-first regardless of ownership.
- Alternatively/also, surface an explicit error/response to the evicted request's callback at eviction time instead of silently deleting it, so victims get an immediate, distinguishable error rather than a timeout.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` with `MaxSavedCallbacks` set low (e.g., 10) for test speed.
2. Call `HandleLegacyUserMessage` once for a "victim" message (`MessageId = "victim"`, signed with a distinct key), capturing its `callback`.
3. Immediately flood `HandleLegacyUserMessage` with `MaxSavedCallbacks` additional distinct signed "attacker" messages (`MessageId = "attacker-N"`, each with a freshly generated key), causing `len(h.savedCallbacks) > MaxSavedCallbacks`.
4. Call `handler.pruneCallbacks()` directly (bypassing the timer) and assert via `handler.savedCallbacks` (under `handler.mu`) that the "victim" entry is gone while several "attacker-N" entries remain (oldest-first eviction).
5. Simulate the DON's legitimate response for "victim" by calling `handler.HandleNodeMessage` with a `MethodWebAPITrigger` response bearing `MessageId = "victim"`; assert it returns `nil` without error but the victim's `callback.Wait(ctx)` never receives a response (times out), proving the response is unrecoverably dropped.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-420)
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
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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
