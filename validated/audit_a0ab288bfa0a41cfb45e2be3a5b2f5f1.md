### Title
Missing allowlist/authorization check before fanning out `web_api_trigger` requests to all DON members - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
`handler.HandleLegacyUserMessage` validates only that a message is well-formed, non-stale, and carries a valid signature (`common.ValidatedRequestFromMessage`), but performs no check that the signer is authorized to publish to the requested `Topics` or to trigger any particular workflow before broadcasting the request to every member of the DON via `don.SendToNode`. The `// TODO: apply allowlist and rate-limiting here` comment at [1](#0-0)  documents this gap, and it is corroborated by an explicit `// TODO: Validate Senders and rate limit check` left in the handler's own test suite.

### Finding Description
`HandleLegacyUserMessage` decodes the JSON payload into `webapicap.TriggerRequestPayload` (which includes an attacker-controlled `Topics` list), checks that `Timestamp` is present and not stale, and then — immediately after the `TODO: apply allowlist and rate-limiting here` comment — verifies only that `msg.Body.Method == MethodWebAPITrigger` before calling `common.ValidatedRequestFromMessage(msg)` and fanning the request out to every DON member: [2](#0-1) 

`common.ValidatedRequestFromMessage` performs no authorization logic at all — it only checks that `MessageId`/`Method` are non-empty and marshals the message into a JSON-RPC request: [3](#0-2) 

The only cryptographic gate that exists before this point is `msg.Validate()` (invoked earlier in the message pipeline, e.g. via the gateway HTTP entrypoint), which merely confirms the message carries a syntactically valid signature recoverable to *some* address — it does not check that the signer/address is registered, owns the requested `Topics`, or is entitled to trigger any workflow. There is no lookup against a per-topic or per-workflow allowlist anywhere in this code path (unlike the newer v2 `httpTriggerHandler.authorizeRequest`, which calls `workflowMetadataHandler.Authorize` and rejects unauthorized signers — see `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go` lines 80-108). Consequently, any client capable of producing a validly signed `api.Message` (using any ECDSA keypair, not one registered for a specific workflow/topic) can set `Topics` to any value and have the gateway forward `MethodWebAPITrigger` to all DON members, letting the DON nodes act on it as a legitimate trigger event for those topics.

The existing test file for this handler already flags the missing enforcement explicitly: [4](#0-3) 
and the happy-path test demonstrates unconditional fan-out to all DON members for any signed trigger message without checking sender/topic ownership: [5](#0-4) 

### Impact Explanation
This is an authorization-bypass / allowlist-bypass issue in the legacy webAPI-trigger gateway path. An unprivileged, unregistered signer can cause the gateway to broadcast an arbitrary `web_api_trigger` message (for topics they do not own) to every node in the DON, potentially causing DON nodes to start workflow executions or consume webAPI trigger capability resources for topics/workflows the attacker has no legitimate relationship to. This maps to Chainlink's "unauthorized job run" / access-control bypass impact class in the gateway/capabilities component, since it allows request impersonation across topic/workflow boundaries without any legitimate registration.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs the ability to generate an ECDSA keypair and sign a well-formed `api.Message` — no registration, no allowlisting, and no privileged credentials are required. The check that is bypassed (`TODO: apply allowlist and rate-limiting here`) is provably absent in code, and it is reachable directly via the gateway's standard node-message ingress path (`HandleLegacyUserMessage`), which is the intended entrypoint for external HTTP clients using the legacy webAPI trigger method. This is trivially and repeatably reproducible.

### Recommendation
Before calling `don.SendToNode` for `MethodWebAPITrigger`, implement the allowlist check referenced in the TODO: verify the message signer against a per-topic or per-workflow authorized-keys registry (mirroring the pattern already implemented in `v2/workflow_metadata_handler.go`'s `Authorize` function), and add per-sender rate limiting, rejecting the request with an appropriate JSON-RPC error before any fan-out occurs if the signer is not authorized for the requested topics.

### Proof of Concept
Go handler-level test plan (extending `handler_test.go`):
1. Call `setupHandler(t)` to construct a `handler` with a mocked `handlers.DON`.
2. Generate an unrelated/unregistered ECDSA key not present in any `donConfig.Members` or authorized-keys structure (the handler under test has no such structure at all, confirming there's nothing to register against).
3. Build a signed `api.Message` via the existing `triggerRequest` helper using this unregistered key and an arbitrary `Topics` list (e.g., `[]string{"someone_elses_workflow_topic"}`) not associated with the signer.
4. Set `don.EXPECT().SendToNode(...)` mock expectations for all DON members (as in the existing `TestHandlerReceiveHTTPMessageFromClient` "happy case").
5. Call `handler.HandleLegacyUserMessage(ctx, msg, cb)` and assert `err == nil` and that `don.AssertExpectations(t)` passes — i.e., the message was forwarded to every DON member despite the signer having no allowlist entry for the requested topics.
6. Document via test comment that this demonstrates the missing allowlist check referenced by the `// TODO: apply allowlist and rate-limiting here` comment.

### Citations

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

**File:** core/services/gateway/handlers/common/message_util.go (L82-105)
```go
// ValidatedRequestFromMessage converts a legacy Gateway Message to a JSON-RPC request
func ValidatedRequestFromMessage(msg *api.Message) (*jsonrpc.Request[json.RawMessage], error) {
	if msg == nil {
		return nil, errors.New("nil message")
	}
	if msg.Body.MessageId == "" {
		return nil, errors.New("message ID is empty")
	}
	if msg.Body.Method == "" {
		return nil, errors.New("method is empty")
	}
	params, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal message: %w", err)
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	return req, nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L236-265)
```go
func TestHandlerReceiveHTTPMessageFromClient(t *testing.T) {
	handler, _, don, nodes := setupHandler(t)
	ctx := t.Context()
	msg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "", "")
	codec := api.JsonRPCCodec{}

	t.Run("happy case", func(t *testing.T) {
		// sends to 2 dons
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()

		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, msg, cb)
		require.NoError(t, err)

		resp, err := hc.ValidatedResponseFromMessage(msg)
		require.NoError(t, err)
		err = handler.HandleNodeMessage(ctx, resp, nodes[0].Address)
		require.NoError(t, err)

		r, err := cb.Wait(t.Context())
		require.NoError(t, err)
		require.Equal(t, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError}, r)
	})
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
