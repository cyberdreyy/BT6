### Title
Callback response hijacking via attacker-chosen colliding `messageId` in `handler.NewHandler` legacy web-API trigger flow - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores each user's callback in a shared map keyed solely by the attacker-supplied `msg.Body.MessageId`, with no check that the ID is not already in use by another in-flight request. Any client submitting a signed request with the same `messageId` as a victim's in-flight request overwrites the victim's saved callback, causing the DON node's eventual response (correlated only by that same `messageId`) to be delivered to the attacker's connection instead of the victim's.

### Finding Description
The public gateway user endpoint decodes an incoming JSON-RPC request and, for legacy DON-ID requests, sets the internal `api.Message`'s `MessageId` directly from the caller-controlled `req.ID`: [1](#0-0)  and `Message.Validate()` only enforces length/format constraints, not uniqueness or ownership: [2](#0-1) .

`gateway.ProcessRequest` routes legacy requests (those with a `DonId`) straight to `h.HandleLegacyUserMessage(ctx, msg, callback)` without any global de-duplication of `messageId` across concurrent callers: [3](#0-2) .

Inside `HandleLegacyUserMessage`, the handler unconditionally inserts (or overwrites) the entry in the shared `savedCallbacks` map keyed by `msg.Body.MessageId`, with no check for an existing/in-flight entry: [4](#0-3) 

This is in contrast to sibling handlers in the same codebase that explicitly guard against this class of bug — e.g. the v2 HTTP trigger handler rejects a second request with the same ID while the first is in flight (`"in-flight request"`), and the vault handler rejects duplicates (`"request was already authorized previously"` / `"request ID already exists"`): [5](#0-4) [6](#0-5) [7](#0-6) 

No such guard exists in the legacy `capabilities` handler's `HandleLegacyUserMessage`.

When a DON node later responds, `handleWebAPITriggerMessage` looks up and deletes `h.savedCallbacks[msg.Body.MessageId]` and delivers the payload to whichever `Callback` is stored there at that time — with no verification that the callback belongs to the request that actually produced this `messageId` on the node side, and no check that the response's sender/content matches the originating requester: [8](#0-7) 

**Exploit flow:**
1. A victim sends a signed legacy `web_api_trigger` request with `messageId = M`, which is broadcast to all DON members and a callback is saved under `savedCallbacks[M]` (line 412).
2. Before the DON responds, an attacker (any client capable of producing a validly signed gateway message with their own key — signature validity is per-message, not tied to the `messageId`) sends another legacy request with the identical `messageId = M`.
3. `HandleLegacyUserMessage` (line 412) overwrites `savedCallbacks[M]` with the attacker's `Callback`, discarding the victim's.
4. When the DON node responds for the original request (still referencing `messageId = M`, since the node just echoes back whatever ID was forwarded to it), `handleWebAPITriggerMessage` looks up `savedCallbacks[M]`, finds the attacker's callback, and delivers the victim's response payload to the attacker's HTTP connection.
5. Signature checks on the node→gateway message only validate that a DON member signed it — they do nothing to bind the response to the correct original requester's `Callback` object, since correlation is by `messageId` alone.

Since `messageId` has no server-side generation, no per-connection namespacing, and no uniqueness enforcement in this handler, this is a straightforward IDOR/race on the correlation key.

### Impact Explanation
If a workflow or capability response ever contains sensitive computed data (e.g., decrypted secrets, vault contents, or DKG-derived material returned via the web-API trigger/target response), the attacker receives that payload directly in their own HTTP response instead of the legitimate requester. This matches "cross-user response confusion" / unauthorized disclosure of another user's request result, and could rise to the "server credential/secret disclosure" class if the underlying capability response payload includes decrypted secrets or key material, though the concrete secret material returned by `web_api_trigger` payloads is workflow-dependent and this handler itself is the routing/correlation layer.

### Likelihood Explanation
Exploitation only requires an unprivileged account capable of sending signed legacy gateway messages to the public user endpoint (any key can sign — the gateway does not perform account-based authorization on `messageId`, only structural/signature validation on the message itself). The attacker needs to guess or observe the target `messageId` and time their request to land after the victim's request is stored but before the DON responds — a straightforward race condition, especially if `messageId`s are predictable (sequential counters, timestamps, or client-supplied UUIDs an attacker can guess or is told). This is a deterministic bug (last-write-wins in `savedCallbacks`), so no probabilistic/network-timing exploit is needed beyond ordinary racing, and it's fully repeatable.

### Recommendation
- Reject `HandleLegacyUserMessage` calls whose `messageId` already exists in `savedCallbacks` (return a conflict error to the caller), mirroring the guard already implemented in the v2 HTTP trigger handler and the vault/confidential-relay handlers.
- Additionally/alternatively, scope `savedCallbacks` keys by `(sender, messageId)` or bind the stored callback to the expected `Sender`/session and verify it on delivery in `handleWebAPITriggerMessage`, so a colliding `messageId` from a different sender cannot overwrite another user's in-flight callback.
- Consider generating/prefixing `messageId` server-side (or hashing in the caller's public key/session) rather than trusting the fully attacker-controlled `req.ID`.

### Proof of Concept
Go handler-level concurrency test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Create a `handler` via `NewHandler` with a mocked `handlers.DON`.
2. Victim: call `HandleLegacyUserMessage(ctx, victimMsg, victimCallback)` with `MessageId = "shared-id"`, valid signature from victim's key — assert `don.SendToNode` was invoked for all DON members and `savedCallbacks["shared-id"]` holds victim's callback.
3. Attacker: before any node response arrives, call `HandleLegacyUserMessage(ctx, attackerMsg, attackerCallback)` with the same `MessageId = "shared-id"` but signed by a different (attacker) key — assert this call either (a) succeeds and overwrites `savedCallbacks["shared-id"]` (demonstrating the bug), or, after the fix, (b) is rejected with a conflict error and the victim's callback remains intact.
4. Simulate a DON node response for `messageId = "shared-id"` via `HandleNodeMessage`.
5. Assert (pre-fix): `attackerCallback.Wait(ctx)` receives the response payload while `victimCallback.Wait(ctx)` times out/never resolves — proving cross-user delivery.
6. Assert (post-fix): the attacker's second `HandleLegacyUserMessage` call is rejected, and `victimCallback.Wait(ctx)` correctly receives the node's response.

### Citations

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

**File:** core/services/gateway/gateway.go (L250-273)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L317-355)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L682-726)
```go
	t.Run("unhappy path - duplicate requestId", func(t *testing.T) {
		h, callback, don, _ := setupHandler(t)
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

		requestID := "1"
		reqData := &vaultcommon.ListSecretIdentifiersRequest{
			RequestId: requestID,
			Owner:     owner,
		}
		reqDataBytes, err := json.Marshal(reqData)
		require.NoError(t, err)

		validJSONRequest := jsonrpc.Request[json.RawMessage]{
			ID:     requestID,
			Method: vaulttypes.MethodSecretsList,
			Params: (*json.RawMessage)(&reqDataBytes),
		}

		responseData := &vaultcommon.ListSecretIdentifiersResponse{
			Identifiers: []*vaultcommon.SecretIdentifier{
				{
					Key:       "foo",
					Owner:     owner,
					Namespace: "default",
				},
			},
		}
		resultBytes, err := json.Marshal(responseData)
		require.NoError(t, err)
		expectedRequestID := owner + vaulttypes.RequestIDSeparator + requestID
		response := jsonrpc.Response[json.RawMessage]{
			ID:     expectedRequestID,
			Result: (*json.RawMessage)(&resultBytes),
			Method: vaulttypes.MethodSecretsList,
		}
		resultBytes, err = json.Marshal(responseData)
		require.NoError(t, err)

		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.NoError(t, err)

		// send duplicate request
		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.ErrorContains(t, err, "request was already authorized previously")

```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```
