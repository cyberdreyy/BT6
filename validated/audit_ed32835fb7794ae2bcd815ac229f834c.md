### Title
Legacy Web-API Trigger Path Forwards Unauthenticated User Messages to All DON Nodes Without Sender Authorization - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` accepts any incoming user message that reaches the gateway and, after only checking payload decoding, timestamp presence, and staleness, forwards it as a `web_api_trigger` request to every member of the DON — without verifying that the caller (`msg.Body.Sender`/requester) is authorized to trigger the target workflow.

### Finding Description
This is structurally analogous to the reported `CredibleAccountModule.preCheck()`/`postCheck()` bug: a caller-supplied identity/target (there, the `wallet`; here, the triggering user/request) is trusted and acted upon (accumulating locked tokens / dispatching a workflow trigger to all nodes) without validating that the actual caller is authorized for that target.

In `HandleLegacyUserMessage`: [1](#0-0) 
the code only unmarshals the payload and checks `payload.Timestamp`. It then checks staleness: [2](#0-1) 
Immediately after, there is an explicit `// TODO: apply allowlist and rate-limiting here` comment, followed by only a method-name check, before the message is transformed into a JSON-RPC request and broadcast to every configured DON member: [3](#0-2) 

This contrasts with the newer, hardened paths in the same codebase that were clearly designed to close exactly this class of bug:
- The v2 HTTP trigger handler requires JWT signature verification and checks the signer against a per-workflow authorized-key set before dispatch: [4](#0-3) 
- The webapi trigger connector handler checks `trigger.allowedSenders[sender.String()]` using a cryptographically authenticated sender (derived from signature verification in `Message.Validate()`) before dispatching to registered workflows: [5](#0-4) 
- The Vault gateway path explicitly binds and validates the request owner against the authorized/authenticated identity via `validateSecretOwnersMatchAuthorized` and `DeriveJWTAuthorizedVaultWorkflowOwner`: [6](#0-5) 

`HandleLegacyUserMessage`, however, has none of this: no signature-to-authorized-key binding, no sender/owner allowlist check, and the TODO comment is direct in-code evidence that this authorization step was never implemented for this legacy code path, despite the design pattern being applied elsewhere in the same package/family of handlers.

### Impact Explanation
Because the message is broadcast to all DON member nodes as an accepted `web_api_trigger` request without proving the caller is entitled to trigger the targeted workflow, an unprivileged/unauthenticated client reaching this legacy endpoint could cause unauthorized job/workflow execution requests to be dispatched to node operators — an unauthorized job run, matching the "unauthorized job run" acceptance criterion. The severity depends on whether this legacy handler path is still reachable/enabled in current gateway deployments (its Methods() still lists `MethodWebAPITrigger` alongside `MethodWebAPITarget`/`MethodComputeAction`/`MethodWorkflowSyncer`, and `HandleLegacyUserMessage` remains part of the `Handler` interface used by `multihandler.go`/`gateway.go`) — but the code path itself, as written, has no request-level authorization.

### Likelihood Explanation
Likelihood is limited by uncertainty over whether the legacy path is still exposed/used in current production gateway configurations versus the newer v2 HTTP trigger handler (which does perform proper authorization). I could not confirm from the index whether `HandleLegacyUserMessage` is still wired to a live, externally reachable endpoint in the current deployment topology or whether it is a deprecated/internal-only code path being phased out in favor of `v2/http_trigger_handler.go`. This uncertainty should be resolved before treating this as a confirmed exploitable issue.

### Recommendation
Add sender/requester authorization to `HandleLegacyUserMessage` before dispatching to DON nodes — mirroring the JWT-signer-to-authorized-key binding used in `workflow_metadata_handler.go`'s `Authorize()` or the `allowedSenders` check in `trigger.go`. At minimum, implement the allowlist/rate-limiting referenced by the existing TODO comment, and verify that the authenticated message sender is permitted to trigger the specific workflow before broadcasting the request to DON members.

### Proof of Concept
Not independently verifiable from the indexed code alone — a concrete PoC would require confirming (a) that this legacy handler is reachable from an unauthenticated/external gateway endpoint in the deployed configuration, and (b) that no upstream middleware (outside the indexed files) performs the missing allowlist check. This should be validated with a live/full-repo review (e.g., a Devin session) tracing the actual gateway request routing from the public HTTP endpoint into `HandleLegacyUserMessage`.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-420)
```go
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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
```

**File:** core/capabilities/webapi/trigger/trigger.go (L97-112)
```go
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

```

**File:** core/capabilities/vault/authorizer.go (L148-152)
```go
// validateSecretOwnersMatchAuthorized checks that secret identifiers in the request payload
// match the authorized workflow owner. This is read-only validation; owner prefixing and
// param stamping happen later in GatewayVaultRequestProcessor.
func validateSecretOwnersMatchAuthorized(req jsonrpc.Request[json.RawMessage], workflowOwner string) error {
	switch req.Method {
```
