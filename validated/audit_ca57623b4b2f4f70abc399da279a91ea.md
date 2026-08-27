### Title
Legacy gateway path forwards signed-but-unauthorized sender requests to handlers with no gateway-level allow-list check - ([File: core/services/gateway/gateway.go])

### Summary
In `gateway.ProcessRequest`, the legacy path (`isLegacyRequest == true`) only calls `msg.Validate()` before dispatching to `h.HandleLegacyUserMessage`. `Validate()` checks field lengths/formats and recovers `msg.Body.Sender` from the ECDSA signature via `ExtractSigner`, but never checks that the recovered `Sender` is a member of the target DON. Authorization is left entirely to the handler implementation, and at least one production handler (`capabilities.handler.HandleLegacyUserMessage`) explicitly defers this with a `// TODO: apply allowlist and rate-limiting here` comment, meaning any holder of a valid ECDSA keypair can produce a well-formed signature and reach handler logic for any configured DON.

### Finding Description
`gateway.ProcessRequest` (core/services/gateway/gateway.go:250-262) treats a request as legacy when `msg.Body.DonId != ""`. It calls `msg.Validate()` [1](#0-0) , which lives in `api/message.go`. `Validate()` enforces signature length/field-length constraints and calls `ExtractSigner()` to recover the sender address from the ECDSA signature, storing it in `m.Body.Sender` [2](#0-1) . Crucially, `Validate()` never compares `Sender` against any DON member/allow-list — it only proves that the signature is internally consistent with *some* key, not that the key is authorized.

After `Validate()` succeeds, `gateway.go` looks up the handler purely by `msg.Body.DonId` (`g.handlers[handlerKey]`) and immediately calls `h.HandleLegacyUserMessage(ctx, msg, callback)` [3](#0-2)  with no gateway-level authorization gate on `Sender`.

The `Handler` interface itself documents no contract requiring a Sender/allow-list check [4](#0-3) , and the `capabilities` handler's `HandleLegacyUserMessage` implementation confirms the check is not implemented: it validates payload structure, timestamp freshness, and method name, but explicitly marks the allow-list as a TODO before forwarding the request to all DON members via `don.SendToNode` [5](#0-4) .

Consequently, an attacker who signs a `MessageBody` with an arbitrary/unregistered private key, sets `DonId` to a real configured DON, and submits it over the gateway's user HTTP endpoint will pass `Validate()` (signature is cryptographically valid over the body) and have the message dispatched to `HandleLegacyUserMessage`, which will forward the (unauthorized) request onward to all DON nodes.

### Impact Explanation
This is a broken-authorization / allow-list-bypass condition: the gateway conflates "signature format/recovery succeeded" with "sender is authorized for this DON," per the question's stated invariant. For handlers that don't perform their own allow-list check (the capabilities handler explicitly does not, per its TODO), an attacker with no privileged credentials can inject requests that get relayed to every node in a target DON, potentially triggering unwanted HTTP fan-out/side effects (`handleWebAPIOutgoingMessage` sends requests to arbitrary URLs specified in the attacker payload) and consuming DON/node resources under the guise of a "signed" request. This matches the "allowlist/quota bypass" and "unauthorized action" impact classes.

### Likelihood Explanation
Precondition is trivial: the attacker only needs any ECDSA keypair (self-generated, free) and knowledge of a real `DonId` (DON IDs are part of public/discoverable gateway configuration, not secret). No operator, node, or DON credential is required — this is purely an unprivileged external client of the gateway's public user-facing HTTP endpoint. The attack is fully repeatable and deterministic (sign → submit → dispatched).

### Recommendation
Add an explicit sender-authorization check in `gateway.ProcessRequest` (or as a mandatory step before invoking `HandleLegacyUserMessage`) that verifies `msg.Body.Sender` against the DON's configured member/allow-list for `msg.Body.DonId`, independent of handler implementation. Alternatively, make allow-list verification a required, testable contract of the `Handler` interface and audit all implementations (including `capabilities.handler`) to remove the outstanding "TODO: apply allowlist" gap before the code path is considered production-safe.

### Proof of Concept
Go handler-level integration test plan:
1. Configure a `capabilities.handler` (or a stub `handlers.Handler` implementing `HandleLegacyUserMessage` with no allow-list check) for DON `"donA"` with a fixed set of member node addresses that does NOT include the attacker's address.
2. Generate an attacker ECDSA keypair unrelated to any configured DON member/user allow-list.
3. Construct `api.Message{Body: api.MessageBody{MessageId: "id1", Method: capabilities.MethodWebAPITrigger, DonId: "donA", Payload: <valid TriggerRequestPayload with recent Timestamp>}}` and sign it with `msg.Sign(attackerPrivKey)`.
4. Call `gateway.ProcessRequest(ctx, encodedRequest, "")` directly (or via `NewGateway`/`NewGatewayFromConfig` test harness).
5. Assert: `msg.Validate()` succeeds and populates `Body.Sender` = attacker address (confirms signature-only check passes).
6. Assert: `HandleLegacyUserMessage` is invoked (e.g., via a mock/spy handler or by observing `don.SendToNode` calls to DON members) despite the attacker's address not being present in any DON allow-list/member config.
7. Expected (vulnerable) result: request is forwarded/processed with no `api.UnauthorizedError`/`api.ErrorCode` rejection prior to handler invocation, demonstrating that authorization is not enforced at the `gateway.go` layer and is silently skipped by handlers that don't implement their own check.

### Citations

**File:** core/services/gateway/gateway.go (L251-262)
```go
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

**File:** core/services/gateway/gateway.go (L264-269)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```

**File:** core/services/gateway/api/message.go (L82-88)
```go
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/handlers/handler.go (L31-52)
```go
type Handler interface {
	job.ServiceCtx

	// Each user request is processed by a separate goroutine, which:
	//   1. calls HandleUserMessage
	//   2. waits on callbackCh with a timeout
	HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error

	// Each user request is processed by a separate goroutine, which:
	//   1. calls HandleUserMessage
	//   2. waits on callbackCh with a timeout
	HandleJSONRPCUserMessage(ctx context.Context, jsonRequest jsonrpc.Request[json.RawMessage], callback Callback) error

	// Handlers should not make any assumptions about goroutines calling HandleNodeMessage.
	// should be non-blocking
	// should validate the message inside the response
	HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error

	// The methods support by this Handler.
	// Should be globally unique across all handlers.
	Methods() []string
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
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
