### Title
Legacy web-API gateway handler dispatches trigger requests to the DON with no per-owner rate limit or allowlist, enabling unlimited unpaid DON execution - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The legacy capabilities handler's `HandleLegacyUserMessage`, which is the public gateway user endpoint entrypoint for `MethodWebAPITrigger`, forwards every accepted request to all DON members without any per-owner/per-sender quota or allowlist check. The `sendHTTPMessageToClient` outbound call and the node-side dispatch in `handleWebAPIOutgoingMessage` are only protected by a `nodeRateLimiter` keyed on the DON node address, not on the authenticated requester/owner, so a client can rotate identifiers freely and still trigger unlimited DON executions and outbound HTTP calls.

### Finding Description
`HandleLegacyUserMessage` (core/services/gateway/handlers/capabilities/handler.go:341) is the entrypoint invoked when an external client submits a signed message for the `MethodWebAPITrigger` method. It performs payload decoding, a staleness check on `payload.Timestamp`, and method validation, but the code literally contains `// TODO: apply allowlist and rate-limiting here` right before the method check [1](#0-0) . No quota, allowlist, or per-owner tracking keyed on the verified sender/owner is performed before the message is converted via `common.ValidatedRequestFromMessage` and sent to every DON member with `don.SendToNode` [2](#0-1) .

Once a DON node processes the trigger and issues an outgoing action/target/compute request, `handleWebAPIOutgoingMessage` is invoked, but its only limiter is `h.nodeRateLimiter.Allow(nodeAddr)`, keyed on the responding DON node's address, not the original requester's identity [3](#0-2) . `sendHTTPMessageToClient` then performs the actual outbound HTTP call with no additional owner-scoped check [4](#0-3) .

Because quotas are only ever keyed on `nodeAddr` (a fixed, small set of trusted DON nodes) and never on the authenticated message sender/owner, an attacker who signs requests with a fresh key for each submission incurs no cumulative quota penalty — each new "owner" identity starts with a fresh allowance, and the node-level limiter is shared across all users of a given node rather than being per-owner. This matches the described exploit: "rotate identifiers" to bypass any owner-scoped quota, because no owner-scoped quota exists at all in this legacy path.

This is corroborated by the newer v2 implementation (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`), which explicitly implements a `userRateLimiter` keyed on `workflowOwner` via `checkRateLimit` [5](#0-4) , and its README documents "Rate Limiting: Multi-dimensional rate limiting (global, per workflow owner, per capability node)" as a designed security feature [6](#0-5) . The legacy handler in scope for this question lacks this owner-scoped control entirely, confirmed by its own TODO comment.

### Impact Explanation
Any client capable of producing a validly-signed gateway message (no privileged credential required beyond generating an EOA-style key) can submit unlimited `web_api_trigger` messages, each of which is forwarded to every DON member and can result in DON compute/execution and outbound network calls via `sendHTTPMessageToClient`. Because there is no per-owner quota, rotating the signing identity for each submission (or simply submitting many requests) allows an attacker to consume DON execution resources and outbound bandwidth without being charged or throttled per caller, an "unpaid/unauthorized DON execution beyond the caller's entitlement" impact, matching the High severity rate-limit-violation class.

### Likelihood Explanation
Exploitation only requires the ability to construct and sign a well-formed gateway message meeting `payload.Timestamp` freshness and method requirements — no special role, admin access, or node compromise is needed. The missing check is explicit and unconditional (the TODO comment shows it was never implemented for this path), so the attack is trivially repeatable and requires no timing or race condition; each request or a burst of them will simply be forwarded to the DON with no owner-based throttling.

### Recommendation
Implement per-owner/per-sender rate limiting and allowlisting in `HandleLegacyUserMessage` before requests are dispatched to DON members, analogous to the v2 `httpTriggerHandler.checkRateLimit`/`userRateLimiter` design: derive a verified owner identity from the message signature, key a rate limiter (and optionally a workflow/owner allowlist) on that identity, and reject/queue requests exceeding the owner's quota prior to calling `don.SendToNode`. Additionally, ensure `handleWebAPIOutgoingMessage`/`sendHTTPMessageToClient` propagate and enforce the original owner's quota rather than relying solely on `nodeRateLimiter` keyed by `nodeAddr`.

### Proof of Concept
Go handler-level integration test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a permissive `NodeRateLimiter` and a `donConfig` with 1+ members backed by a mock `handlers.DON`.
2. Generate N distinct signing keys (simulating "rotated identifiers"). For each key, build a validly-signed `api.Message` with `MethodWebAPITrigger`, a fresh `MessageId`, and `payload.Timestamp = time.Now()`.
3. For each of the N messages (using a different signer each time), call `handler.HandleLegacyUserMessage(ctx, msg, callback)` and assert `don.SendToNode` (mock expectation) is invoked for every single request — i.e., no requests are rejected due to quota.
4. Assert that despite N being arbitrarily large (e.g., N=10,000) and no requests sharing a signer, none receive `api.RateLimitError` or similar rejection, demonstrating the absence of per-owner throttling.
5. Contrast with `TestHttpTriggerHandler_HandleUserTriggerRequest_RateLimiting` in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go` [7](#0-6) , which shows the v2 handler correctly rejects excess requests via `jsonrpc.ErrLimitExceeded` — the legacy handler's PoC should show the equivalent check never fires.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L121-146)
```go
func (h *handler) sendHTTPMessageToClient(ctx context.Context, req network.HTTPRequest, msg *api.Message) (*api.Message, error) {
	var payload Response
	resp, err := h.httpClient.Send(ctx, req)
	if err != nil {
		return nil, err
	}
	payload = Response{
		ExecutionError: false,
		StatusCode:     resp.StatusCode,
		Headers:        resp.Headers,
		Body:           resp.Body,
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	return &api.Message{
		Body: api.MessageBody{
			MessageId: msg.Body.MessageId,
			Method:    msg.Body.Method,
			DonId:     msg.Body.DonId,
			Payload:   payloadBytes,
		},
	}, nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L371-396)
```go
func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		if errLimited, ok := errors.AsType[limits.ErrorRateLimited](err); ok {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L16-23)
```markdown
### 1.2 Core Functionality

- **HTTP Actions**: Outbound HTTP requests with caching, rate limiting
- **HTTP Triggers**: Inbound requests that trigger workflow executions with JWT-based authentication
- **Auth Metadata Management**: Collection and aggregation of workflow authorization data
- **Response Caching**: Caching of HTTP responses to reduce redundant requests
- **Rate Limiting**: Multi-dimensional rate limiting (global, per workflow owner, per capability node)
- **Response Aggregation**: Byzantine fault-tolerant aggregation of node responses
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1907-2017)
```go
func TestHttpTriggerHandler_HandleUserTriggerRequest_RateLimiting(t *testing.T) {
	cfg := ServiceConfig{
		CleanUpPeriodMs:             60000,
		MaxTriggerRequestDurationMs: 300000,
	}

	donConfig := &config.DONConfig{
		DonId: "test-don",
		F:     1,
		Members: []config.NodeConfig{
			{Address: "node1"},
			{Address: "node2"},
			{Address: "node3"},
		},
	}

	mockDon := handlermocks.NewDON(t)
	lggr := logger.Test(t)
	metadataHandler := createTestMetadataHandler(t)
	testMetrics := createTestMetrics(t, donConfig)

	t.Run("successful rate limit check with CRE context", func(t *testing.T) {
		userRateLimiter := createTestUserRateLimiter() // Unlimited
		handler := newTestTriggerHandler(t, lggr, cfg, donConfig, mockDon, metadataHandler, userRateLimiter, testMetrics)

		privateKey := createTestPrivateKey(t)
		workflowID := "0x1234567890abcdef1234567890abcdef12345678901234567890abcdef123456"
		workflowOwner := "0x1234567890abcdef1234567890abcdef12345678"

		// Register workflow with reference
		registerWorkflow(t, handler, workflowID, privateKey)
		handler.workflowMetadataHandler.workflowIDToRef[workflowID] = workflowReference{
			workflowOwner: workflowOwner,
			workflowName:  "test-workflow",
			workflowTag:   "v1.0",
		}

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
			ID:      "test-request-id",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		req.Auth = createTestJWTToken(t, req, privateKey)

		callback := hc.NewCallback()

		// Mock DON to expect sends to all nodes
		mockDon.EXPECT().SendToNode(mock.Anything, "node1", mock.Anything).Return(nil)
		mockDon.EXPECT().SendToNode(mock.Anything, "node2", mock.Anything).Return(nil)
		mockDon.EXPECT().SendToNode(mock.Anything, "node3", mock.Anything).Return(nil)

		err = handler.HandleUserTriggerRequest(t.Context(), req, callback, time.Now())
		require.NoError(t, err)
	})

	t.Run("rate limit exceeded returns proper error", func(t *testing.T) {
		// Create a rate limiter with very restrictive limits
		restrictiveRateLimiter := limits.WorkflowRateLimiter(1, 0)
		handler := newTestTriggerHandler(t, lggr, cfg, donConfig, mockDon, metadataHandler, restrictiveRateLimiter, testMetrics)

		privateKey := createTestPrivateKey(t)
		workflowID := "0x1234567890abcdef1234567890abcdef12345678901234567890abcdef123456"
		workflowOwner := "0x1234567890abcdef1234567890abcdef12345678"

		// Register workflow with reference
		registerWorkflow(t, handler, workflowID, privateKey)
		handler.workflowMetadataHandler.workflowIDToRef[workflowID] = workflowReference{
			workflowOwner: workflowOwner,
			workflowName:  "test-workflow",
			workflowTag:   "v1.0",
		}

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
			ID:      "test-request-id-rate-limit",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		req.Auth = createTestJWTToken(t, req, privateKey)

		callback := hc.NewCallback()

		// First request should consume the burst capacity and exceed the rate limit
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback, time.Now())
		require.Error(t, err)
		r, err := callback.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrLimitExceeded)
	})
}
```
