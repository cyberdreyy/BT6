### Title
Signed gateway message envelope lacks enforced single-use nonce, and `HandleLegacyUserMessage` silently overwrites `savedCallbacks[MessageId]` on replay, enabling duplicate DON-wide dispatch of a previously valid signed request - (File: core/services/gateway/api/message.go, core/services/gateway/handlers/capabilities/handler.go)

### Summary
`Sign`/`SignKS` compute the signature only over `GetRawMessageBody(&m.Body)` = `{MessageId, Method, DonId, Receiver, Payload}`, with no envelope-level nonce/expiry enforcement mechanism tied to signature freshness. In `HandleLegacyUserMessage`, `MessageId` is used only as a map key for `savedCallbacks` and is unconditionally overwritten (`h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`) with no existence check, so replaying the exact same signed bytes is never rejected and re-dispatches the request to every DON member again.

### Finding Description
`GetRawMessageBody` in [1](#0-0)  defines exactly the signed field set (`MessageId`, `Method`, `DonId`, `Receiver`, `Payload`), consumed identically by `Sign`, `SignKS`, and `ExtractSigner` at [2](#0-1) . There is no signature-embedded issued-at/expiry, and `MessageId` uniqueness is not enforced by the signing scheme itself — it's purely up to each consumer.

Cross-referencing consumers:
- `HandleLegacyUserMessage` (capabilities webapi-trigger handler) stores the callback keyed only by `MessageId` and overwrites unconditionally with no duplicate check: [3](#0-2) . Replaying an identical valid signed message a second time (before the DON has responded) silently drops/orphans the first callback and re-sends the trigger request to every DON member again via the loop at [4](#0-3) .
- The only replay mitigation present is an application-level payload timestamp check bounded by `h.config.MaxAllowedMessageAgeSec`, at [5](#0-4)  — this bounds, but does not eliminate, the replay window; it is not part of `GetRawMessageBody`'s signed-field-set contract and is handler-specific, not universal.
- `common.RequestCache.NewRequest`, used by other handler paths, DOES reject duplicate `MessageId` while a request is pending via the `globalId{sender, id}` map key check "request already exists": [6](#0-5) . This path is replay-safe only while the original entry is still pending; once it completes/times out and is deleted (`deleteAndSendOnce`), the same `MessageId`+bytes can be resent and treated as brand new.
- `gateway.ProcessRequest` performs no dedup/replay checks of its own; it purely calls into the handler for both legacy and JSON-RPC paths: [7](#0-6) .

Replay-safety matrix:
| Path | MessageId uniqueness enforced? | Time-bound? |
|---|---|---|
| `HandleLegacyUserMessage` (capabilities handler, `savedCallbacks`) | No (silent overwrite) | Yes, but only via payload-level `Timestamp` field, handler-specific |
| `common.RequestCache.NewRequest` | Yes, while pending | No (once completed/evicted, ID is reusable) |
| `gateway.ProcessRequest` | No (delegates entirely) | No |

### Impact Explanation
The concrete, reachable impact is limited to duplicate/repeated processing of the same signed request within the DON, and orphaning of a prior pending callback in the `savedCallbacks` overwrite case (each replay drops the earlier caller's response channel). This can cause duplicate trigger execution across DON nodes for `HandleLegacyUserMessage`-routed requests, matching the "unauthorized/duplicate job run" impact class. It does **not** constitute privilege escalation, key/secret disclosure, or cross-user response confusion, since `MessageId`/`Sender` remain cryptographically bound and no other user's data is exposed.

### Likelihood Explanation
Exploitation requires only possession of previously valid signed message bytes (no key compromise), consistent with an unprivileged holder of a signed gateway request replaying it. However, actual severity is constrained: for `HandleLegacyUserMessage` the payload-embedded timestamp check bounds the exploit window to `MaxAllowedMessageAgeSec`, so this is not "unlimited" replay as characterized in the question — it is a bounded-window duplicate-dispatch issue plus a permanent lack of duplicate rejection (overwrite instead of reject).

### Recommendation
Add explicit duplicate-`MessageId` rejection (not silent overwrite) in `HandleLegacyUserMessage`'s `savedCallbacks` map, and consider embedding an explicit timestamp/nonce field directly in `MessageBody`/`GetRawMessageBody` so replay protection is enforced uniformly by the signing/verification layer rather than left to each handler's ad hoc application logic.

### Proof of Concept
Table test plan (Go):
1. Build a signed `api.Message` via `Sign()`.
2. Call `HandleLegacyUserMessage` twice with the identical message/signature bytes before the first callback resolves; assert `don.SendToNode` mock is invoked twice (once per replay) and the first callback never receives a response (orphaned), demonstrating overwrite-not-reject behavior — extending the existing test at [8](#0-7) .
3. Separately exercise `common.RequestCache.NewRequest` with the same `(sender, MessageId)` twice concurrently, asserting the second call returns `"request already exists"` — confirming this path is replay-safe only while pending, per [9](#0-8) .

### Citations

**File:** core/services/gateway/api/message.go (L96-134)
```go
func (m *Message) Sign(privateKey *ecdsa.PrivateKey) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := gw_common.SignData(privateKey, rawData...)
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(crypto.PubkeyToAddress(privateKey.PublicKey).Hex())
	return nil
}

func (m *Message) SignKS(ctx context.Context, ks keys.MessageSigner, signer common.Address) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := ks.SignMessage(ctx, signer, gw_common.Flatten(rawData...))
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(signer.Hex())
	return nil
}

func (m *Message) ExtractSigner() (signerAddress []byte, err error) {
	if m == nil {
		return nil, errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signatureBytes, err := hex.DecodeString(m.Signature)
	if err != nil {
		return nil, err
	}
	return gw_common.ExtractSigner(signatureBytes, rawData...)
}
```

**File:** core/services/gateway/api/message.go (L136-146)
```go
func GetRawMessageBody(msgBody *MessageBody) [][]byte {
	alignedMessageId := make([]byte, MessageIdMaxLen)
	copy(alignedMessageId, msgBody.MessageId)
	alignedMethod := make([]byte, MessageMethodMaxLen)
	copy(alignedMethod, msgBody.Method)
	alignedDonId := make([]byte, MessageDonIdMaxLen)
	copy(alignedDonId, msgBody.DonId)
	alignedReceiver := make([]byte, MessageReceiverLen)
	copy(alignedReceiver, msgBody.Receiver)
	return [][]byte{alignedMessageId, alignedMethod, alignedDonId, alignedReceiver, msgBody.Payload}
}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-419)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-63)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```

**File:** core/services/gateway/gateway.go (L264-276)
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
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L339-363)
```go
	t.Run("savedCallbacks stored only when message is valid", func(t *testing.T) {
		require.Empty(t, handler.savedCallbacks)

		invalidPayloadMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", `{"foo":"bar"}`)
		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, invalidPayloadMsg, cb)
		require.NoError(t, err)
		_, _ = cb.Wait(t.Context())

		staleMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "123456", "")
		cb2 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, staleMsg, cb2)
		require.NoError(t, err)
		_, _ = cb2.Wait(t.Context())

		badMethodMsg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "foo", "", "")
		cb3 := hc.NewCallback()
		err = handler.HandleLegacyUserMessage(ctx, badMethodMsg, cb3)
		require.NoError(t, err)
		_, _ = cb3.Wait(t.Context())

		handler.mu.Lock()
		require.Empty(t, handler.savedCallbacks, "error paths must not leave entries in savedCallbacks")
		handler.mu.Unlock()
	})
```

**File:** core/services/gateway/handlers/common/requestcache_test.go (L125-141)
```go
func TestRequestCache_MaxSize(t *testing.T) {
	t.Parallel()

	cache := common.NewRequestCache[requestState](time.Hour, 2)
	callback := common.NewCallback()
	lggr := logger.Test(t)
	initialState := &requestState{}

	req := &api.Message{Body: api.MessageBody{MessageId: "aa", Sender: "0x1234"}}
	require.NoError(t, cache.NewRequest(lggr, req, callback, initialState))

	req.Body.MessageId = "bb"
	require.NoError(t, cache.NewRequest(lggr, req, callback, initialState))

	req.Body.MessageId = "cc"
	require.Error(t, cache.NewRequest(lggr, req, callback, initialState))
}
```
