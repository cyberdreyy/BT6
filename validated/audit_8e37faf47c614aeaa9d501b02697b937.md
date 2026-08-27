### Title
Unauthenticated MessageId Collision Allows Cross-User Response Hijacking in Legacy WebAPI Capabilities Gateway Handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The legacy WebAPI capabilities gateway handler stores pending user request callbacks in a map keyed only by the client-supplied `MessageId`, without checking whether that ID is already associated with an in-flight request from a different requester. This mirrors the report's core bug class: a request/response record is keyed by an attacker-influenceable identifier rather than being bound to the original requester, allowing a malicious client to hijack or DOS a response destined for someone else's request.

### Finding Description
In `HandleLegacyUserMessage`, the handler unconditionally writes to the shared `savedCallbacks` map using the incoming message's `MessageId` with no existence check: [1](#0-0) 

This is directly analogous to the Oracle `internalId`/callback-mapping issue in the report: any caller can choose the key (`MessageId`) that indexes into the callback table, and there is no ownership/ownership check tying that key to the original request's caller before a later response gets routed. If a second `web_api_trigger` request arrives (from any client, since this method has "TODO: apply allowlist and rate-limiting here" and no auth binding of MessageId-to-sender) with the same `MessageId` as an already in-flight legitimate request, the map entry — and thus the pending `Callback` — for the first request is silently overwritten: [2](#0-1) 

When a DON node later replies with that `MessageId` (validated only by `msg.Body.Sender != nodeAddr`, i.e., node-level authenticity, not per-request ownership), `handleWebAPITriggerMessage` looks the response up purely by `MessageId` and delivers it to whichever callback currently sits in the map: [3](#0-2) 

The original legitimate caller's HTTP connection would then hang (until pruning) or receive no response, while the attacker's callback intercepts the node's reply — cross-user response confusion/DOS, entirely analogous to the "malicious callback address hijacking a Chainlinked request" pattern described in the report.

Notably, the newer v2 pipeline (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`) already recognizes and closes this exact gap: it explicitly rejects a second request using an ID that is already in flight with a `jsonrpc.ErrConflict` / "in-flight request" error, as shown by the test: [4](#0-3) 

This confirms the maintainers treat "duplicate/collided request ID from an unprivileged caller" as a real threat requiring an explicit guard — a guard that is absent in the legacy `handler.go` path that is still present and wired into `Methods()`/`HandleNodeMessage`.

### Impact Explanation
An unprivileged external client (any party able to submit `web_api_trigger`/legacy webAPI messages to the gateway) can:
- Deny service to a legitimate requester by squatting on/guessing their `MessageId` and stealing the eventual node response.
- Potentially receive a response payload intended for another user's workflow trigger, which — depending on downstream consumption of `web_api_trigger` results — could leak trigger/workflow response content to an unauthorized party.

The severity is capped by the fact that `MessageId` values would need to collide (either through prediction of low-entropy/sequential IDs used by legitimate callers, such as the example script's default `id=12345`, or through a race where the attacker observes an ID and races to submit before the legitimate response returns).

### Likelihood Explanation
Reaching this code path requires only sending a JSON-RPC/legacy message to the gateway's public endpoint with method `web_api_trigger`; the handler comment itself notes `// TODO: apply allowlist and rate-limiting here`, and there is no per-request authorization binding `MessageId` to a specific sender in this legacy path before storing the callback. Likelihood of exploitation depends on the caller's ability to learn or predict another party's in-flight `MessageId` (e.g., low-entropy or shared testing IDs, or a race window during concurrent DON member fan-out), which is plausible but not universally guaranteed for all callers.

### Recommendation
- In `HandleLegacyUserMessage`, reject (or otherwise safely handle) requests whose `MessageId` already exists in `savedCallbacks`, mirroring the v2 `httpTriggerHandler`'s in-flight conflict check.
- Bind the stored callback entry to the authenticated identity/session of the original caller (not just the raw `MessageId`), and validate that identity when delivering the node's response, consistent with how `HandleNodeMessage` already checks `msg.Body.Sender != nodeAddr` for node authenticity.
- Consider migrating remaining legacy WebAPI trigger traffic onto the v2 `http_trigger_handler.go` pipeline, which already implements this protection.

### Proof of Concept
1. Legitimate user A sends a `web_api_trigger` request to the gateway with `MessageId = "12345"`; the handler stores `savedCallbacks["12345"] = callbackA`.
2. Before the DON responds, attacker B sends another `web_api_trigger` request with the same `MessageId = "12345"`; the handler overwrites `savedCallbacks["12345"] = callbackB` with no conflict check (`core/services/gateway/handlers/capabilities/handler.go:411-414`).
3. When a DON node subsequently responds with `MessageId = "12345"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["12345"]`, finds `callbackB`, and delivers the node's response to attacker B instead of legitimate user A (`core/services/gateway/handlers/capabilities/handler.go:148-161`). User A's request now silently times out / never resolves.

Note: I was unable to fully verify from the index alone whether any request-level authentication or per-session binding exists further upstream in the gateway HTTP server that would tie a specific `MessageId` to a specific authenticated caller before reaching `HandleLegacyUserMessage` (the `ProcessRequest` dispatch code I inspected in `core/services/gateway/gateway.go` shows no such binding, but a full audit of `network`/HTTP-layer auth middleware for this legacy endpoint was not completed within available tool calls). If such binding exists elsewhere, it would need to be confirmed to fully assess exploitability; if it does not, the finding stands as described.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
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
