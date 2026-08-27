This confirms the finding. `Message.Validate()` at `core/services/gateway/api/message.go:54-88` only checks signature format and extracts the signer address — it does not verify the signer is a known/allowlisted trigger user. The gateway's `ProcessRequest` at `core/services/gateway/gateway.go:250-269` calls `msg.Validate()` (signature well-formedness only) then routes directly to `h.HandleLegacyUserMessage`, and `HandleLegacyUserMessage` itself performs no sender/allowlist check before dispatching to every DON member.

### Title
Missing allowlist enforcement in HandleLegacyUserMessage allows unauthorized DON trigger dispatch - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`(*handler).HandleLegacyUserMessage` accepts any validly-signed `web_api_trigger` message and forwards it to every DON member via `don.SendToNode`, with no check that the message sender is an authorized/allowlisted workflow owner. The explicit `// TODO: apply allowlist and rate-limiting here` comment at line 384 confirms the control is absent on this path.

### Finding Description
The gateway's HTTP entrypoint `ProcessRequest` (`core/services/gateway/gateway.go:250-269`) decodes an incoming JSON-RPC request into an `api.Message`, calls `msg.Validate()`, and for legacy DON-ID requests routes straight to `h.HandleLegacyUserMessage(ctx, msg, callback)`. `Message.Validate()` (`core/services/gateway/api/message.go:54-88`) only checks field lengths/format and calls `ExtractSigner()` to populate `m.Body.Sender` — it verifies the signature is cryptographically valid but performs no check that the recovered signer address is a permitted/known workflow owner.

Inside `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`), the checks performed are: payload decodable, `Timestamp != 0`, and message not stale. Immediately after these, at line 384, the comment `// TODO: apply allowlist and rate-limiting here` marks the exact spot where sender-based authorization should occur but does not. The method then proceeds to build a request via `common.ValidatedRequestFromMessage(msg)` and unconditionally loops over `h.donConfig.Members`, calling `don.SendToNode(ctx, member.Address, req)` for every DON node — regardless of who the sender is.

Because any client can generate an ECDSA keypair, self-sign a `web_api_trigger` `api.Message` (satisfying `Validate()`), and submit it to the gateway's HTTP endpoint, an attacker with zero prior registration can cause the gateway to dispatch a trigger job to the entire DON under their own (self-chosen) sender address.

### Impact Explanation
This matches the "unauthorized job run" / DON capacity abuse impact class: an arbitrary, unregistered externally-owned address can force every node in a DON to process and execute a web API trigger request it should never have accepted, consuming node compute/HTTP-egress capacity and callback/state resources (`h.savedCallbacks`) attributable to an attacker who was never allowlisted for that DON/workflow. This is a real authorization-bypass on the gateway's trust boundary, not a mock/config/best-practice issue — the enforcement code is simply missing.

### Likelihood Explanation
Feasible with no privilege whatsoever: the attacker only needs network access to the gateway HTTP endpoint and can generate their own private key locally (`msg.Sign(privateKey)` in `core/services/gateway/api/message.go:96-108`). The existing test `triggerRequest` helper in `handler_test.go` demonstrates the exact minimal signed-message construction required. This is fully repeatable per request and is limited only by the (separate, no-op-relevant) per-node rate limiter that applies to outgoing HTTP messages from DON nodes, not to inbound trigger submission.

### Recommendation
Implement the allowlist check at the marked TODO in `HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:384`): after extracting `msg.Body.Sender`, verify it against a configured/authorized sender allowlist (e.g., per-DON workflow owner list) and reject with an appropriate `api` error code (e.g., `api.UnauthorizedError`) before calling `common.ValidatedRequestFromMessage` and `don.SendToNode`. Additionally apply per-sender rate-limiting as the TODO indicates, mirroring the existing `h.nodeRateLimiter` pattern used for outgoing messages.

### Proof of Concept
Go test plan (extends `handler_test.go`):
1. In `TestHandlerReceiveHTTPMessageFromClient`, add a subtest `"sad case unregistered sender not allowlisted"`.
2. Generate a fresh ECDSA key not present in `h.donConfig` or any DON allowlist config (`unregisteredKey, _ := crypto.GenerateKey()`).
3. Build a message via the existing `triggerRequest(t, unregisteredKey, []string{"daily_price_update"}, "", "", "")` helper.
4. Set up `don.EXPECT().SendToNode(...)` to assert it is **never called** (`.Times(0)` / omit any `.On` and use `AssertNotCalled`).
5. Call `err := handler.HandleLegacyUserMessage(ctx, msg, cb)` and wait on `cb.Wait(...)`.
6. Expected (secure) behavior: response has an authorization-error `ErrorCode` (e.g., a new `UnauthorizedError`) and `don.SendToNode` is never invoked.
7. Current (vulnerable) behavior: test fails because `SendToNode` is called for every `h.donConfig.Members` entry despite `unregisteredKey` never being allowlisted, confirming the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L193-234)
```go
func triggerRequest(t *testing.T, key *ecdsa.PrivateKey, topics []string, methodName, timestamp, payload string) *api.Message {
	messageID := "12345"
	if methodName == "" {
		methodName = MethodWebAPITrigger
	}
	if timestamp == "" {
		timestamp = strconv.FormatInt(time.Now().Unix(), 10)
	}
	donID := "workflow_don_1"
	var payloadJSON []byte
	if payload == "" {
		ts, err := strconv.ParseInt(timestamp, 10, 64)
		require.NoError(t, err)
		reqPayload := webapicap.TriggerRequestPayload{
			TriggerId:      "web-api-trigger@1.0.0",
			TriggerEventId: "action_1234567890",
			Timestamp:      ts,
			Topics:         topics,
			Params: webapicap.TriggerRequestPayloadParams(map[string]any{
				"bid": "101",
				"ask": "102",
			}),
		}
		payloadJSON, err = json.Marshal(reqPayload)
		require.NoError(t, err)
	} else {
		payloadJSON = []byte(payload)
	}
	msg := &api.Message{
		Body: api.MessageBody{
			MessageId: messageID,
			Method:    methodName,
			DonId:     donID,
			Payload:   json.RawMessage(payloadJSON),
		},
	}
	err := msg.Sign(key)
	require.NoError(t, err)
	err = msg.Validate()
	require.NoError(t, err)
	return msg
}
```
