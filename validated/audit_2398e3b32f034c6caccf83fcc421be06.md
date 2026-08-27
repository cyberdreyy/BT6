### Title
Unauthenticated allowlist/rate-limit bypass in gateway capabilities `HandleLegacyUserMessage` allows any signer to trigger DON capability execution - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` verifies only message structure/signature well-formedness (via `msg.Validate()` upstream) and timestamp freshness, then reaches an explicit `// TODO: apply allowlist and rate-limiting here` comment with no enforcement code, before saving a callback and broadcasting the request to every DON member via `don.SendToNode`. Any address that can produce a validly-signed, non-stale `MethodWebAPITrigger` message can consume gateway/DON capability execution resources regardless of subscription or allowlist status.

### Finding Description
The request path is: `gateway.ProcessRequest` (core/services/gateway/gateway.go:218-292) decodes the JSON-RPC body, and for legacy DON-ID requests calls `msg.Validate()` [1](#0-0)  which only checks field lengths/null-byte suffixes and recovers the signer address from the ECDSA signature via `ExtractSigner` [2](#0-1) . It performs no membership/allowlist check against any subscriber list — it merely proves *a* valid key signed the message, not that the key is authorized for the DON.

The gateway then dispatches to the resolved handler's `HandleLegacyUserMessage` [3](#0-2) . In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` performs payload decoding, a `Timestamp` presence check, and a staleness check [4](#0-3) , then hits the literal comment `// TODO: apply allowlist and rate-limiting here` immediately followed only by a method-name check [5](#0-4) . No allowlist, subscription, or per-sender rate-limit check exists anywhere in this function. The function then registers the callback and fans the request out to every DON member: [6](#0-5) .

Note that `h.nodeRateLimiter` exists on the `handler` struct [7](#0-6)  but is only invoked in `handleWebAPIOutgoingMessage`, the node→gateway path, not in the user-facing `HandleLegacyUserMessage` path [8](#0-7) . This confirms rate-limiting is applied to node-originated traffic but is absent for arbitrary externally-signed user triggers.

### Impact Explanation
Any EOA capable of generating an ECDSA signature (i.e., any external actor with no relationship to the DON's subscriber/allowlist) can force the gateway to broadcast a `web_api_trigger` request to every member node of a targeted DON and consume a `savedCallbacks` slot. This maps to unauthorized consumption of DON capability execution resources — a denial-of-service / resource-exhaustion class impact, and an authorization-exactness violation since only allowlisted/subscribed senders should be able to trigger DON capability execution.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only an ECDSA keypair (trivially generated), knowledge of a valid `DonId` string, and the ability to craft a `TriggerRequestPayload` with a current `Timestamp`. No subscription, credential, or prior interaction with the gateway is required. The attack is fully repeatable and scriptable, requiring only that the timestamp stay within `MaxAllowedMessageAgeSec`.

### Recommendation
Implement the allowlist/rate-limiting check at the marked TODO location in `HandleLegacyUserMessage`, verifying `msg.Body.Sender` (populated by `Validate()`) against the DON's configured allowlist/subscription list before saving the callback or calling `don.SendToNode`, and apply a per-sender rate limiter (analogous to `h.nodeRateLimiter`) to this path. Reject unauthorized senders with `api.UnauthorizedError` (or equivalent) prior to any DON broadcast or `savedCallbacks` mutation.

### Proof of Concept
Go handler-level test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build `setupHandler(t)` as in existing tests, giving a `don` mock and DON config with known member addresses.
2. Generate a fresh, arbitrary private key not present in any allowlist/subscriber config (`crypto.GenerateKey()`), distinct from `nodes[...]` keys used in `triggerRequest`.
3. Construct a message via `triggerRequest(t, unregisteredKey, []string{"daily_price_update"}, "", "", "")`, ensuring `Timestamp` is current so the staleness check passes.
4. Set expectations `don.On("SendToNode", ...).Return(nil)` for each `donConfig.Members` entry.
5. Call `handler.HandleLegacyUserMessage(ctx, msg, cb)`.
6. Assert `err == nil` and that `don.SendToNode` was invoked once per DON member (via mock assertions), and that `handler.savedCallbacks[msg.Body.MessageId]` is populated — demonstrating the request was forwarded and a callback registered despite the signer having no allowlist entry, confirming absence of authorization enforcement.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
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
```

**File:** core/services/gateway/gateway.go (L264-273)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-61)
```go
type handler struct {
	services.StateMachine
	config          HandlerConfig
	don             handlers.DON
	donConfig       *config.DONConfig
	savedCallbacks  map[string]*savedCallback
	mu              sync.Mutex
	lggr            logger.Logger
	httpClient      network.HTTPClient
	nodeRateLimiter *ratelimit.RateLimiter
	wg              sync.WaitGroup
	stopCh          services.StopChan
	metrics         *metrics
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-383)
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
