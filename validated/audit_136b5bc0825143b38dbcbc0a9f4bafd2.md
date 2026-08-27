### Title
No allowlist/subscription authorization enforced before dispatching legacy Web API trigger requests to all DON nodes - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` validates only message structure, method name, and timestamp freshness, then forwards the raw request to every DON member via `don.SendToNode`, with no allowlist, subscription, or per-sender authorization check. The TODO at line 384 confirms this gap is intentional/unfinished, and the code between it and dispatch performs no such check.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) decodes any inbound HTTP/WS request and, for legacy (DonId-populated) messages, calls only `msg.Validate()` [1](#0-0)  — a purely cryptographic/structural check (`api.Message.Validate` and `ExtractSigner`), not an authorization/allowlist decision [2](#0-1) . It then invokes `h.HandleLegacyUserMessage(ctx, msg, callback)` [3](#0-2) .

Inside `HandleLegacyUserMessage`, the checks performed are: payload decodable, `payload.Timestamp != 0`, message not stale, and `msg.Body.Method == MethodWebAPITrigger` [4](#0-3) . Immediately after these checks and directly above the DON dispatch loop sits the explicit `// TODO: apply allowlist and rate-limiting here` comment [5](#0-4) , and no allowlist, subscription lookup, or sender-authorization call exists anywhere in the function before `don.SendToNode` is invoked for every DON member [6](#0-5) .

This is confirmed by contrast with the sibling `vault` handler in the same package tree, which enforces an `Authorizer`/allowlist (`requestProcessor.ProcessRequest`) before performing any node-facing action [7](#0-6)  — no equivalent authorization call exists in `capabilities/handler.go`'s legacy path. The `nodeRateLimiter` field on the handler is only applied to node-originated outgoing messages (`handleWebAPIOutgoingMessage`), not to inbound legacy user messages [8](#0-7) .

Thus, any client able to reach the gateway's HTTP/WS endpoint with a syntactically valid `api.Message` (correctly hex-encoded signature format, non-empty MessageId/Method/DonId, fresh timestamp) targeting `MethodWebAPITrigger` will have that message signed/forwarded and dispatched to every configured DON member (`h.donConfig.Members`), triggering workflow/job execution on the DON regardless of whether the sender holds any authorized Functions/CRE subscription or allowlist entry.

### Impact Explanation
This matches the Chainlink bounty class of "unauthorized job run" / allowlist bypass: an unauthenticated (or merely correctly-signed-but-unauthorized) actor can force execution of arbitrary workflows across an entire DON, consuming node compute/gas resources and bypassing intended billing/subscription gating for the Web API trigger capability. This is a request-impersonation/authorization-bypass class issue, not a denial-of-service or config issue, since it grants meaningful unauthorized capability invocation.

### Likelihood Explanation
The only precondition is producing a well-formed `api.Message` with a correctly formatted 65-byte-style hex signature (any valid ECDSA keypair works — `Validate()` only extracts the signer, it does not check the signer is on an allowlist) and a fresh timestamp, then sending it to the gateway's user-facing endpoint. No credentials, roles, or prior authorization state are required, and the flow is fully repeatable per request, making this a low-effort, high-repeatability path for any external actor able to reach the gateway.

### Recommendation
Implement the allowlist/authorization check called out by the TODO before the DON dispatch loop in `HandleLegacyUserMessage`: after signature/method/timestamp validation, look up the extracted sender (`msg.Body.Sender`) against the configured allowlist/subscription registry for the target DON/workflow and reject with an authorization error (mirroring the `vault` handler's `requestProcessor.ProcessRequest`/`Authorizer` pattern) if not authorized. Also apply the existing `nodeRateLimiter` (or an equivalent per-sender limiter) to this inbound path.

### Proof of Concept
Add a handler-level test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a mock `handlers.DON` (e.g. from `core/services/gateway/handlers/mocks`).
2. Build a valid `api.Message` signed with a fresh, arbitrary (never-allowlisted) ECDSA key, `Method = MethodWebAPITrigger`, and a `webapicap.TriggerRequestPayload` with `Timestamp = time.Now().Unix()`.
3. Call `handler.HandleLegacyUserMessage(ctx, msg, mockCallback)`.
4. Assert that `mockDON.SendToNode` was called once per `donConfig.Members` entry (`don.SendToNode` invoked N times) despite the signer never appearing in any allowlist/subscription configuration — demonstrating dispatch occurs unconditionally, confirming no authorization gate exists.

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

**File:** core/services/gateway/gateway.go (L267-269)
```go
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-396)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-419)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
```
