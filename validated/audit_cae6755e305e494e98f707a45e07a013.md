### Title
Cross-user response confusion via user-controlled `MessageId` collision in legacy WebAPI capabilities gateway handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The legacy WebAPI capabilities gateway handler stores per-request callbacks in a shared map keyed solely by the client-supplied `MessageId` (derived from the JSON-RPC `req.ID`), with no check for an already in-flight request with the same ID, unlike its v2 counterpart which explicitly rejects duplicate in-flight IDs. This allows two different unprivileged callers to collide on the same `MessageId`, causing the DON node's response intended for the first caller to be delivered to the second caller (or vice versa) — a direct analog of the reported smart-contract bug class where non-atomic state cleanup lets a stale/foreign entry be reused across "parties," causing cross-party data confusion.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` unconditionally overwrites `h.savedCallbacks[msg.Body.MessageId]` with the new caller's callback, regardless of whether an entry already exists for that key: [1](#0-0) 

The `MessageId` used as the map key comes directly from the untrusted JSON-RPC request `ID` field, which is fully attacker/client-controlled and set on the message with no uniqueness enforcement at this layer: [2](#0-1) 

When the DON node eventually responds with `MethodWebAPITrigger`, the handler looks up and deletes whatever callback is currently stored under that `MessageId` and delivers the response to it — without verifying that the callback belongs to the original requester: [3](#0-2) 

Contrast this with the newer v2 HTTP trigger handler, which explicitly detects and rejects a duplicate/in-flight request ID with a JSON-RPC conflict error before proceeding, precisely to prevent this class of bug: [4](#0-3) 

This is structurally analogous to the reported liquidation bug: a shared piece of state (`partyBAllocatedBalances`/OpenPositions there; `savedCallbacks[MessageId]` here) is not atomically or exclusively cleaned/isolated per actor before being reused, so a second unrelated actor's operation can silently consume or overwrite state belonging to a different actor, producing cross-actor confusion of results.

### Impact Explanation
If Caller A sends a legacy `web_api_trigger` request with `MessageId = "X"` and, before A's DON response arrives, Caller B (any unprivileged client able to reach the gateway's legacy JSON-RPC endpoint) sends another request reusing `MessageId = "X"`, B's callback silently replaces A's in `savedCallbacks`. When the DON node responds for the trigger tied to ID "X" (originally A's), the handler delivers that response to B's callback instead of A's. A's original callback is discarded and never resolved (it will only be dropped later by `pruneCallbacks` on age/size limits), so A receives no response (denial of service / hang) while B receives a response that does not belong to their request (cross-user response confusion, potential data leakage of A's DON response content to B, or vice versa depending on timing). This falls squarely within "cross-user response confusion," an explicitly accepted impact category.

### Likelihood Explanation
Likelihood depends on: (1) whether the legacy handler path (`HandleLegacyUserMessage`, methods `MethodWebAPITarget`/`MethodWebAPITrigger`/etc.) remains reachable from an external, unprivileged endpoint in current deployments, since a newer v2 HTTP handler with proper duplicate-ID rejection exists alongside it; and (2) an attacker's ability to predict or race a victim's `MessageId`. Because `MessageId` is a plain user-supplied string with no server-side uniqueness/session binding, and no collision defense exists at this layer (unlike v2), the precondition is trivial to construct if two distinct clients pick the same ID — this could happen accidentally (colliding client-generated IDs, e.g., low-entropy or reused IDs from a buggy client) or be deliberately triggered by a malicious client racing a known/guessable ID used by another workflow node.

### Recommendation
- Reject `HandleLegacyUserMessage` requests whose `MessageId` already has an active entry in `savedCallbacks`, mirroring the v2 `httpTriggerHandler`'s duplicate/in-flight request check, returning a JSON-RPC conflict error instead of overwriting.
- Alternatively, key `savedCallbacks` by a server-generated identifier or a composite of `(caller identity, MessageId)` rather than trusting the raw client-supplied ID alone.
- Add explicit test coverage (as already exists for v2) asserting that a second request with a colliding `MessageId` is rejected and does not silently overwrite the first caller's pending callback.

### Proof of Concept
1. Deploy/enable the legacy WebAPI capabilities handler path so that `HandleLegacyUserMessage` is reachable via the gateway's JSON-RPC endpoint for `MethodWebAPITrigger`.
2. Caller A sends a JSON-RPC request with `id = "shared-id"`, method `web_api_trigger`; the handler stores A's callback at `h.savedCallbacks["shared-id"]` and forwards the request to all DON nodes (`core/services/gateway/handlers/capabilities/handler.go:411-420`).
3. Before a DON node responds, Caller B sends another JSON-RPC request with the same `id = "shared-id"` (trivial since no uniqueness check exists); B's callback overwrites A's entry at the same map key.
4. A DON node responds for the original (A's) trigger with `MessageId = "shared-id"`. `handleWebAPITriggerMessage` fetches and deletes `h.savedCallbacks["shared-id"]`, which now holds B's callback, and delivers A's DON response to B (`core/services/gateway/handlers/capabilities/handler.go:148-162`).
5. Caller A receives no response for their original request (hangs until later evicted by `pruneCallbacks`), while Caller B receives a response corresponding to A's request/context — confirming cross-user response confusion.

### Citations

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

**File:** core/services/gateway/handlers/common/message_util.go (L46-52)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
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
