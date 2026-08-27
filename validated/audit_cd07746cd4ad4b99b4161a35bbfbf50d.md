### Title
Legacy gateway message path (`HandleLegacyUserMessage`) forwards signed-but-unauthorized requests to DON nodes without allowlist enforcement - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`gateway.ProcessRequest` routes any request whose `msg.Body.DonId` is non-empty into the legacy path, which only calls `msg.Validate()` (structural + signature-format checks and ECDSA signer recovery) before invoking `handler.HandleLegacyUserMessage`. That handler explicitly skips allowlist enforcement, marked by a `// TODO: apply allowlist and rate-limiting here` comment, and unconditionally forwards the request to every member of the DON.

### Finding Description
In `gateway.ProcessRequest`, when `msg.Body.DonId != ""` the request is treated as legacy: `isLegacyRequest = true`, and the only gate is `msg.Validate()` [1](#0-0) . `Message.Validate()` checks field lengths/format and calls `ExtractSigner()` to recover an ECDSA signer address from the signature over the message body [2](#0-1) . This proves the request was signed by *some* private key, but crucially it does not check that the recovered `Sender` address is a member of the target DON or otherwise on any allowlist.

The request then flows to `handler.HandleLegacyUserMessage` in the capabilities handler, which validates payload structure, timestamp freshness, and method name, but explicitly defers authorization: `// TODO: apply allowlist and rate-limiting here` [3](#0-2) . After these checks it unconditionally forwards the original signed request to every configured DON member: `for _, member := range h.donConfig.Members { ... don.SendToNode(ctx, member.Address, req) }` [4](#0-3) .

Consequently, an attacker who knows a valid `DonId` string need only generate their own ECDSA keypair, sign a well-formed legacy `Message` with a fresh, non-expired timestamp and the `web_api_trigger` method, and the gateway will accept and forward the message to all DON nodes — without any check that the signer is an authorized/allowlisted user of that DON. The non-legacy path (`HandleJSONRPCUserMessage`) is not implemented at all in this handler (`return errors.New("capabilities handler does not support JSON-RPC user messages")`) [5](#0-4) , so a full apples-to-apples comparison of allowlist enforcement between legacy and new paths for this specific handler is not possible from this file alone — the gap is real but is an acknowledged/pending TODO rather than a DonId-spoofing-specific "downgrade" attack.

### Impact Explanation
Any external party who knows a target DON's ID can get arbitrary self-signed "trigger" requests delivered to that DON's nodes without being an allowlisted user, corresponding to Chainlink's allowlist/quota bypass and unauthorized-request-impersonation impact class. Because `handleWebAPITriggerMessage`/`handleWebAPIOutgoingMessage` on the node side may trigger workflow executions or outbound HTTP calls from DON nodes, this could lead to unauthorized job/workflow execution and resource consumption on DON infrastructure.

### Likelihood Explanation
Preconditions are low: an unauthenticated attacker only needs (a) knowledge of a valid `DonId` (often discoverable/config-public) and (b) the ability to generate an ECDSA keypair and sign a message — no privileged credentials or a real allowlist proof are required, since `Validate()`/`ExtractSigner()` only prove possession of *a* key, not membership. The behavior is deterministic and repeatable since it is a code-path property, not a race or timing condition, though it is explicitly called out as unfinished (`TODO`) rather than silently broken security logic.

### Recommendation
Implement allowlist enforcement in `HandleLegacyUserMessage` (and confirm it's applied consistently to `HandleJSONRPCUserMessage` for all handler types) by checking `msg.Body.Sender` (populated during `Validate()`) against the DON's/service's configured allowlist before forwarding to `don.SendToNode`, mirroring whatever authorizer is used for non-legacy requests, and reject with an authorization error otherwise.

### Proof of Concept
1. Build a `handler` with a `donConfig` containing at least one node member and a signature/allowlist enforcement (currently absent).
2. Craft a `api.Message` with `Body.DonId` set to a known/valid DON ID, `Method = MethodWebAPITrigger`, a valid recent `Timestamp`, and sign it with a freshly generated (non-allowlisted) key via `msg.Sign(randomKey)`.
3. Call `gateway.ProcessRequest` with this raw request and assert that it does NOT return an authorization error, and that `don.SendToNode` (mocked) is invoked for DON members — demonstrating the message is forwarded despite the signer not being part of any allowlist.
4. Add a second test where the same message is (hypothetically) submitted with allowlist enforcement to assert it should be rejected, showing the discrepancy fixed by the recommended check.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L295-297)
```go
func (h *handler) HandleJSONRPCUserMessage(_ context.Context, _ jsonrpc.Request[json.RawMessage], _ handlers.Callback) error {
	return errors.New("capabilities handler does not support JSON-RPC user messages")
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-420)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
