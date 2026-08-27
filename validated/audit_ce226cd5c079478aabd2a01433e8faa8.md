### Title
Cross-user callback hijack via `savedCallbacks` keyed only by `MessageId` (not `Sender`) - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`(*handler).HandleLegacyUserMessage` stores the per-request callback in `h.savedCallbacks[msg.Body.MessageId]` without incorporating the sender's identity into the key. Because `MessageId` is attacker-supplied (part of the signed request body but chosen by the client), two different unauthenticated senders can submit requests with the same `MessageId`, and the second submission silently overwrites the first's saved callback, causing the DON's response to the first requester to be delivered to the second requester instead.

### Finding Description
In `HandleLegacyUserMessage` [1](#0-0) , the message payload is decoded and validated, but nothing ties `msg.Body.MessageId` to `msg.Body.Sender`. The callback is stored keyed purely by `MessageId`: [2](#0-1) 

`MessageId` originates from the client-controlled JSON-RPC request ID (`m.Body.MessageId = req.ID` in `ValidatedMessageFromReq`) [3](#0-2) , and `Message.Validate()` only checks length/format constraints on `MessageId`, never uniqueness or binding to the sender [4](#0-3) . The signature (`ExtractSigner`) determines `Sender`, but `Sender` is not part of the map key nor checked against any prior entry for that `MessageId`.

When the DON later responds, `handleWebAPITriggerMessage` looks up and deletes the callback purely by `MessageId` and invokes whichever callback is currently stored: [5](#0-4) 

Exploit flow:
1. Attacker (unauthenticated, only needs a valid DON ID and any keypair to sign) sends message A: `MessageId = "X"`, signed by key A, with payload P_A.
2. `HandleLegacyUserMessage` stores `savedCallbacks["X"] = callback_A` and forwards the request to all DON members.
3. Before the DON responds, attacker (or any other client) sends message B: `MessageId = "X"`, signed by key B (different sender), payload P_B.
4. `HandleLegacyUserMessage` overwrites `savedCallbacks["X"] = callback_B`, silently discarding `callback_A`. Both requests were forwarded to the DON nodes.
5. When a DON node responds to message A's `MessageId` "X", `handleWebAPITriggerMessage` finds `savedCallbacks["X"] == callback_B` and delivers requester A's response data (or a response indexed by A's trigger request) to requester B's HTTP callback. Requester A's original HTTP call never receives a response (hangs until gateway/client timeout).

No check in the reachable path (signature verification, `Validate()`, rate limiter) prevents two different signers from choosing the same `MessageId`; the rate limiter only throttles by node address for outgoing messages, not by sender for legacy user messages [6](#0-5) .

### Impact Explanation
This is a cross-user response confusion / callback hijack: an unauthenticated attacker who merely guesses or reuses a `MessageId` (attacker fully controls their own `MessageId` choice, so it is trivial to collide with a known or predictable ID, or the attacker can simply race a victim's known ID if IDs are otherwise not fully random/secret) can cause another user's DON-derived response to be delivered to the attacker's callback, and/or cause denial of service (the victim's request silently hangs and times out). This matches the "cross-user response confusion" / unauthorized access to another user's data class called out in the audit scope.

### Likelihood Explanation
Preconditions are minimal: unauthenticated HTTP access to the gateway endpoint for a valid DON ID and the ability to produce a validly signed message (signing key can be any keypair — no allowlist check is shown in this path). The attacker needs to know or predict the victim's `MessageId` and win a race to submit their colliding message before the DON responds to the original. If `MessageId` values are client-chosen and not cryptographically random/unpredictable (e.g., sequential or derived from client-visible data), this is straightforward to exploit; if IDs are high-entropy random UUIDs unknown to the attacker, exploitation requires either information leakage of the victim's `MessageId` or the attacker deliberately reusing their own previous ID against a race with an unrelated victim using the same ID by coincidence — this narrows practical likelihood but does not eliminate it, since nothing in the code enforces `MessageId` uniqueness/unpredictability or binds it to `Sender`.

### Recommendation
Key `savedCallbacks` by a composite of `Sender` and `MessageId` (e.g., `sender+":"+messageId`), and reject/reset any existing callback entry when a new legacy user message arrives with a duplicate key from a different (or even the same) sender rather than silently overwriting it. Additionally, verify in `handleWebAPITriggerMessage` that the responding node's message correlates to the expected sender before invoking the stored callback.

### Proof of Concept
Go handler-level test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct two `api.Message` values with identical `Body.MessageId = "dup-id"`, `Body.Method = MethodWebAPITrigger`, but signed by two different ECDSA keys (`keyA`, `keyB`) and different payloads (`payloadA`, `payloadB`).
2. Create two mock `handlers.Callback` implementations, `callbackA` and `callbackB`, each recording whether `SendResponse` was invoked and with what payload.
3. Call `h.HandleLegacyUserMessage(ctx, msgA, callbackA)` then immediately `h.HandleLegacyUserMessage(ctx, msgB, callbackB)`.
4. Assert `h.savedCallbacks["dup-id"].Callback == callbackB` (confirms overwrite occurred) — this demonstrates the flaw at the state level.
5. Simulate a DON node response for `MessageId = "dup-id"` via `h.HandleNodeMessage(ctx, resp, nodeAddr)` and assert that `callbackB.SendResponse` is invoked with the response, while `callbackA.SendResponse` is never called (times out/never invoked) — proving requester A's response never arrives while requester B (a different signer) received the routed response, confirming the isolation break.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-357)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
```go
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
