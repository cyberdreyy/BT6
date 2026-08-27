## Answer

### Title
Legacy gateway user messages route to any known DON without verifying sender entitlement - ([File: core/services/gateway/gateway.go], [File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`message.Sign` (`core/services/gateway/api/message.go`) signs whatever `DonId`/`Receiver`/`Method`/`Payload` the caller puts in `MessageBody`; it authenticates the *sender* but does not bind the sender to a particular DON. `Signature` only proves who signed the message, not that the signer is entitled to address that `DonId` [1](#0-0) . The gateway's legacy request path then selects the handler purely by the client-supplied `DonId` string looked up in a static, publicly-guessable map [2](#0-1) , and the "webapi_trigger" capabilities handler forwards every accepted message to all members of that DON with an explicit `// TODO: apply allowlist and rate-limiting here` [3](#0-2) .

### Finding Description
The signed envelope format is: `Signature` over `(MessageId, Method, DonId, Receiver, Payload)`, produced client-side by `Sign`/`SignKS` [4](#0-3) . `Validate()` only checks field lengths/format and extracts the signer address into `Body.Sender`; it never checks whether `Sender` is allowed to use the requested `DonId` [5](#0-4) .

On the gateway side, `ProcessRequest` treats any non-empty `DonId` as a legacy request: it calls `msg.Validate()` (signature format check only) and then does `h, ok = g.handlers[handlerKey]` where `handlerKey = msg.Body.DonId` [2](#0-1) . `g.handlers` is a static map of all configured DON IDs known to the gateway operator, so any client who knows (or brute-forces/enumerates) a DON ID string can route a request to it — DON IDs are not secrets.

For the capabilities/webapi-trigger legacy handler, `HandleLegacyUserMessage` explicitly marks the allowlist/rate-limit check as unimplemented (`// TODO: apply allowlist and rate-limiting here`) and unconditionally forwards the request to every member node of that DON [3](#0-2) . This confirms the DON selected by the attacker-controlled `DonId` field receives and processes the message without any check that the signer is entitled to use that DON.

Downstream, actual workflow execution is additionally gated by a per-workflow `allowedSenders`/`allowedTopics` allowlist enforced at the node/capability level in `webapiTrigger.processTrigger` [6](#0-5) , so a fully unauthorized workflow cannot ultimately be triggered end-to-end for the "webapi_trigger" flow. This second gate limits impact to gateway/node-level message routing and processing overhead rather than actual unauthorized job execution for that specific capability.

### Impact Explanation
Any internet client that can sign a message (any EOA) can address a `DonId` belonging to a DON they have no relationship with, causing the gateway to route and the capabilities handler to broadcast the message to every member node of that DON, consuming gateway/node bandwidth and per-message processing (decoding, signature verification, dispatch) — a resource-consumption/DoS-adjacent effect on a DON not entrusted to the attacker. Because node-side allowlisting (`allowedSenders`) still gates actual workflow triggering for the webapi-trigger capability, this does not by itself demonstrate full "theft of protocol revenue" (unauthorized paid DON work billed to another owner) for that specific capability; it is best characterized as a missing gateway-level authorization/allowlist control (matching the explicit `TODO` in the code) rather than a demonstrated fund-movement/billing bypass.

### Likelihood Explanation
Preconditions are minimal: any externally-owned key, knowledge of a target `DonId` (not secret — DON IDs are typically visible in job specs/configs), and the ability to POST a signed JSON-RPC/legacy message to the gateway's public user endpoint. No operator, node, or DB access is required, and the request is trivially repeatable/scriptable.

### Recommendation
Add an explicit authorization/entitlement check at the gateway or handler level that verifies the signer (`Body.Sender`) is permitted to address the requested `DonId`/service before forwarding to DON members — i.e., implement the outstanding `// TODO: apply allowlist and rate-limiting here` in `core/services/gateway/handlers/capabilities/handler.go`, and consider validating `DonId` entitlement generically in `gateway.ProcessRequest` for all legacy-routed requests, not only in service-specific handlers that happen to implement their own allowlists (vault, HTTP trigger).

### Proof of Concept
Table-driven test plan (Go, `core/services/gateway` package):
1. Configure a `gateway` with two DON handlers, `donA` (mock) and `donB` (mock), via `g.handlers`.
2. Generate an attacker keypair not associated with either DON's authorized senders/allowlist.
3. Build and `Sign` a `Message` with `Body.DonId = "donB"` (a DON the attacker has no relation to) and a valid `Method`.
4. Call `gw.ProcessRequest` and assert:
   - No `UnsupportedDONIdError` is returned (routing succeeds) — i.e., `handlerKey == "donB"` and `g.handlers["donB"].HandleLegacyUserMessage` is invoked, verified via a mock `Handler.On("HandleLegacyUserMessage", ...)`.
   - For the capabilities handler specifically, assert `don.SendToNode` mock is called for every member of `donB` regardless of `Sender` not being in any allowlist for `donB` (since the current code has no such check), proving unauthorized DON capacity consumption.
5. Add a comparison case where `donB`'s handler contains a proper allowlist to show the fix would reject the request with an authorization error instead of forwarding.

### Citations

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

**File:** core/services/gateway/api/message.go (L90-122)
```go
// Message signatures are over the following data:
//  1. MessageId aligned to 128 bytes
//  2. Method aligned to 64 bytes
//  3. DonId aligned to 64 bytes
//  4. Receiver (in hex) aligned to 42 bytes
//  5. Payload (raw bytes before parsing)
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
```

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

**File:** core/capabilities/webapi/trigger/trigger.go (L79-148)
```go
// processTrigger iterates over each topic, checking against senders and rateLimits, then starting event processing and responding
func (h *triggerConnectorHandler) processTrigger(ctx context.Context, gatewayID string, body *api.MessageBody, sender ethCommon.Address, payload webapicap.TriggerRequestPayload) error {
	// Pass on the payload with the expectation that it's in an acceptable format for the executor
	wrappedPayload, err := values.WrapMap(payload)
	if err != nil {
		return fmt.Errorf("error wrapping payload %w", err)
	}
	topics := payload.Topics

	// empty topics is error for V1
	if len(topics) == 0 {
		return errors.New("empty Workflow Topics")
	}

	// workflows that have matched topics
	matchedWorkflows := 0
	// workflows that have matched topic and passed all checks
	fullyMatchedWorkflows := 0
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
				fullyMatchedWorkflows++
				TriggerEventID := body.Sender + payload.TriggerEventId

				// Emit trigger execution started event
				workflowExecutionID, genErr := events.GenerateExecutionID(trigger.workflowID, TriggerEventID)
				if genErr != nil {
					h.lggr.Errorw("failed to generate execution ID", "err", genErr)
					workflowExecutionID = ""
				}
				emitErr := events.EmitTriggerExecutionStarted(ctx, map[string]string{}, TriggerEventID, workflowExecutionID)
				if emitErr != nil {
					h.lggr.Errorw("failed to emit trigger execution started event", "err", emitErr)
				}

				tr := capabilities.TriggerResponse{
					Event: capabilities.TriggerEvent{
						TriggerType: TriggerType,
						ID:          TriggerEventID,
						Outputs:     wrappedPayload,
					},
				}
				select {
				case <-ctx.Done():
					return nil
				case trigger.ch <- tr:
					// Sending n topics that match a workflow with n allowedTopics, can only be triggered once.
					break
				}
			}
		}
	}
	if matchedWorkflows == 0 {
		return errors.New("no Matching Workflow Topics")
	}

	if fullyMatchedWorkflows > 0 {
		return nil
	}
	return err
```
