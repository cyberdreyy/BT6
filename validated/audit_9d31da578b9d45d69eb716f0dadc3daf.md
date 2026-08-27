I have sufficient evidence to answer now.

### Title
Legacy gateway user requests (`HandleLegacyUserMessage`) accept any valid ECDSA-signed message as authenticated sender with no allowlist enforcement - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`api.Message.Validate()` / `ExtractSigner()` in `core/services/gateway/api/message.go` only verify that the `signature` field is well-formed and cryptographically recoverable to *some* address; they never check that the recovered address belongs to any registered/authorized party. The legacy web-API-trigger path (`handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go`) forwards the message to all DON members using `msg.Body.Sender` as the authenticated identity without any allowlist check, as explicitly flagged by a `TODO` comment in the code.

### Finding Description
`Message.Validate()` (`core/services/gateway/api/message.go:54-88`) performs only format/length checks and then calls `ExtractSigner()`, setting `m.Body.Sender = utils.StringToHex(string(signerBytes))` from whatever key produced a syntactically valid 65-byte signature — no comparison against a known set of DON members, workflow owners, or any allowlist happens at this layer [1](#0-0) .

This message is decoded from the raw JSON-RPC `params` field via `common.ValidatedMessageFromReq` (`core/services/gateway/handlers/common/message_util.go:36-58`), which simply unmarshals attacker-supplied JSON and calls `m.Validate()` [2](#0-1) . The gateway's `ProcessRequest` (`core/services/gateway/gateway.go:250-269`) treats any legacy request (one that carries a `DonId`) by calling `msg.Validate()` and then routing straight into `h.HandleLegacyUserMessage(ctx, msg, callback)` [3](#0-2) .

Inside `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`), the code checks payload decoding, timestamp staleness, and supported method — but explicitly skips sender authorization, marked by the comment `// TODO: apply allowlist and rate-limiting here` right before the method check, and then forwards the request `req` (built from the same unauthenticated `msg`) to every DON member via `don.SendToNode` [4](#0-3) . No allowlist or membership check on `msg.Body.Sender` exists anywhere on this path before the request reaches DON nodes as a `web_api_trigger` capability invocation.

This is asymmetric with the node→gateway direction: `HandleNodeMessage` explicitly checks `if msg.Body.Sender != nodeAddr { return errors.New(...) }` (`core/services/gateway/handlers/capabilities/handler.go:253-255`), confirming that sender-membership verification is understood as necessary elsewhere in the same file but is missing for the user-request path.

### Impact Explanation
An unprivileged attacker who owns any ECDSA keypair can craft a message body, sign it themselves, and have the gateway accept and forward it to the entire DON as an authenticated `web_api_trigger` request under an attacker-chosen `Sender` identity. Downstream (`h.handleWebAPITriggerMessage`), this is treated as a legitimate user-initiated workflow trigger and consumed by the capability node — this maps to Chainlink's "unauthorized job run" / authentication-bypass impact class, since request binding to an authorized/allowlisted sender is never enforced at the gateway layer for this handler.

### Likelihood Explanation
The precondition is trivial: generation of an arbitrary ECDSA keypair (no credentials, no prior registration). The attacker only needs network access to the gateway's user-facing JSON-RPC endpoint. The flaw is deterministic and repeatable for every request, since `Validate()`/`ExtractSigner()` never queries any allowlist and `HandleLegacyUserMessage` never checks one either (per the `TODO`).

### Recommendation
Enforce a sender allowlist check in `handler.HandleLegacyUserMessage` (or a shared authorization layer invoked from `gateway.ProcessRequest`) that validates `msg.Body.Sender` against `h.donConfig`'s configured allowed senders (or workflow registry-derived key set) before forwarding to `don.SendToNode`, matching the pattern already used for JWT-based auth in `capabilities/v2/http_trigger_handler.go`'s `authorizeRequest`/`workflowMetadataHandler.Authorize`.

### Proof of Concept
1. In `core/services/gateway/handlers/capabilities/handler_test.go`, extend `setupHandler` to construct a `handler` with a `donConfig` that does NOT include the test signer's address.
2. Build an `api.Message` with a valid `TriggerRequestPayload` (`Timestamp = time.Now().Unix()`), sign it with a freshly generated `crypto.GenerateKey()` private key via `msg.Sign(privateKey)`.
3. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)`.
4. Assert: (a) no error is returned and `don.SendToNode` mock is invoked for every DON member (proving the unauthorized sender's request was forwarded), and (b) `msg.Body.Sender` equals `strings.ToLower(crypto.PubkeyToAddress(privateKey.PublicKey).Hex())`, i.e., the attacker's own address, with no allowlist rejection occurring.
5. Contrast with a companion assertion that `handler.HandleNodeMessage` DOES reject a mismatched sender (`errors.New("message sender mismatch...")`), highlighting the asymmetry/missing check on the user path.

### Citations

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
