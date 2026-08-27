### Title
`Message.Validate` never checks sender entitlement to `DonId`, allowing any signed request to consume an arbitrary DON's capacity - ([File: core/services/gateway/api/message.go])

### Summary
`Message.Validate` in `core/services/gateway/api/message.go` only checks length/format of `DonId`, `MessageId`, `Method`, `Receiver`, and recovers the signer from the signature; it never verifies that the recovered `Sender` is entitled to use the named `DonId`. Because the gateway's `ProcessRequest` routes legacy requests purely by `msg.Body.DonId` to whichever handler is registered for that ID, and at least one production handler (`WebAPICapabilitiesType`, `core/services/gateway/handlers/capabilities/handler.go`) forwards the message to **all** members of that DON without any allowlist/ownership check, any internet client holding an arbitrary ECDSA key can direct signed work to a DON it has no relationship with.

### Finding Description
The request path is:
1. `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-262`) decodes the request; when `msg.Body.DonId != ""` it treats it as a "legacy" request, calls `msg.Validate()`, then looks up `h, ok = g.handlers[msg.Body.DonId]` — a pure map lookup keyed by the attacker-supplied `DonId`. [1](#0-0) 
2. `Message.Validate` (`core/services/gateway/api/message.go:54-88`) validates only string lengths/suffixes of `MessageId`, `Method`, `DonId`, `Receiver`, checks the signature length, and recovers `m.Body.Sender` from the signature via `ExtractSigner`. There is no check binding `Sender` to `DonId` (e.g., no allowlist/ownership/subscription lookup). [2](#0-1) 
3. Once routed, `handlers.Handler.HandleLegacyUserMessage` is invoked with the message. In the `WebAPICapabilitiesType` handler this method explicitly has a `// TODO: apply allowlist and rate-limiting here` and unconditionally fans the message out to every member of the target DON: `for _, member := range h.donConfig.Members { ... don.SendToNode(ctx, member.Address, req) }`. [3](#0-2) 

Because the routing key (`DonId`) is attacker-controlled and part of the signed payload (so it passes signature verification trivially — the attacker signs their own chosen `DonId`), and because `Validate()`/the legacy handler perform no authorization tying `Sender` to `DonId`, a signer with no relationship to a target DON can still get its request forwarded to that DON's nodes, consuming DON compute/bandwidth capacity that should be reserved for legitimate subscribers of that DON.

This differs from the `vault` handler, which does perform per-request authorization (`h.requestProcessor.ProcessRequest` / `allowListBasedAuth.AuthorizeRequest`) before any node fan-out, tying the workflow owner to allowlisted entries in that DON's on-chain registry. [4](#0-3)  The `WebAPICapabilitiesType` legacy handler lacks the equivalent check.

### Impact Explanation
An unprivileged internet client can select any known/guessable `DonId` in a signed gateway envelope and have the gateway forward the request to that DON's member nodes for the `WebAPICapabilitiesType` handler, without being an authorized user of that DON. This causes DON compute/network resource consumption (fan-out `SendToNode` to every member) attributable to an owner/subscriber that never authorized or requested the work — matching the "theft of protocol revenue / DON work charged to another owner" impact class, since the target DON's operators/subscribers bear the processing cost of the unauthorized request.

### Likelihood Explanation
Exploitation requires only: (1) any ECDSA keypair (no registration needed, since `Validate()`/`Sign()` accept an arbitrary externally-owned key), and (2) knowledge of a valid `DonId` string configured on the gateway. `DonId` values are not secret — they are used in routing responses and appear in configuration/documentation/logs — so this is trivially repeatable via the public gateway HTTP endpoint (`gateway.ProcessRequest`). No operator, admin, or DON-node privilege is needed.

### Recommendation
- Add an authorization step to the legacy `WebAPICapabilitiesType.HandleLegacyUserMessage` path (mirroring the vault handler's `AuthorizeRequest`/allowlist pattern) that verifies the recovered `Sender` is entitled to submit work to the target `DonId` before fanning out to DON nodes.
- Alternatively/additionally, extend `Message.Validate` (or a wrapper called immediately after it in `gateway.ProcessRequest`) to reject requests whose `Sender` is not present in a per-DON allowlist/subscription registry before handler dispatch.
- Remove/resolve the `// TODO: apply allowlist and rate-limiting here` in `core/services/gateway/handlers/capabilities/handler.go`.

### Proof of Concept
Go handler-level test plan (table-driven), extending `core/services/gateway/gateway_test.go` and/or `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Configure a gateway/handler for `donA` (owned by workflow owner A) and a separate handler for `donB` (owned by owner B), each with distinct DON node mocks.
2. Using a private key with no registered relationship to either DON, sign a legacy `Message` with `Body.DonId = "donB"` via `Message.Sign`.
3. Call `gw.ProcessRequest` (or directly `handler.HandleLegacyUserMessage`) with this message and assert:
   - `msg.Validate()` returns `nil` (no error) despite the signer having no entitlement to `donB`.
   - The mock `DON.SendToNode` for all of `donB`'s members is invoked (asserted via `mock.On("SendToNode", ...).Once()` per member), proving the unauthorized DON's nodes processed attacker-controlled work.
4. Add a regression assertion that once a fix (entitlement check) is added, the same request is rejected with an authorization error and no `SendToNode` calls occur.

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

**File:** core/services/gateway/handlers/vault/handler.go (L431-450)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
	if activeRequestErr != nil {
		return activeRequestErr
	}
```
