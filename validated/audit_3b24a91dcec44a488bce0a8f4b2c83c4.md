## Finding: Missing per-owner quota enforcement on confidential-relay gateway requests

### Title
No per-owner rate limiting / quota check before DON fan-out in `confidentialrelay.handler.HandleJSONRPCUserMessage` — ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
The confidential-relay gateway handler forwards every incoming JSON-RPC user request straight to `fanOutToNodes`, which sends the request to every DON member, with no authentication of a caller identity and no quota check tied to any owner/sender before dispatch. The only rate limiters in this handler (`globalNodeRateLimiter`, `perNodeRateLimiters`) throttle *node responses* coming back through `HandleNodeMessage`, not the *incoming* user request that triggers DON execution. `bundler.setSignedResponse` (the code that finally produces the response handed back to the user) contains no quota logic either — it only copies the built response and count into the `BundleSummary`; by the time it runs, DON-side work has already been spent on every submission.

### Finding Description
`HandleJSONRPCUserMessage` at [1](#0-0)  only validates that `req.ID` is non-empty and ≤200 characters, then calls `h.newActiveRequest` and `h.fanOutToNodes`. There is no call to any authorizer, no JWT/owner verification, and no per-sender/per-owner rate limiter comparable to what the vault handler (`h.requestProcessor.ProcessRequest` deriving `authorizedOwner`, see [2](#0-1) ) or the HTTP trigger handler (`checkRateLimit` keyed on `workflowOwner`, see [3](#0-2) ) implement.

The only rate limiters configured for this handler are `globalNodeRateLimiter` and `perNodeRateLimiters`, both consumed inside `HandleNodeMessage` on the DON→gateway response path [4](#0-3) , not on the user→gateway request path. `fanOutToNodes` unconditionally sends the request to every configured DON member [5](#0-4) , i.e., every accepted user request causes full DON-side execution work regardless of who submitted it or how many prior requests they made.

`bundler.setSignedResponse` itself, the function named in the audit target, is a pure bookkeeping setter with no quota semantics: [6](#0-5) . By the time this runs, the DON has already fanned out and executed the request for every node in `donConfig.Members`, so any enforcement here would be too late regardless.

Because the only uniqueness check is `req.ID` (deduplicated per-gateway-process in `h.activeRequests`, see `newActiveRequest` at [7](#0-6) ), an attacker who simply varies `req.ID` on every call defeats that in-flight dedup and triggers a brand-new full DON fan-out with no upper bound tied to caller identity.

### Impact Explanation
Any client capable of reaching this handler's JSON-RPC endpoint can trigger unlimited, unmetered DON executions of `MethodSecretsGet`/`MethodCapabilityExec` by rotating `req.ID` values, with zero identity binding and zero per-owner quota. This matches the "rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement" impact class, since DON compute/signature work is spent per request with no accounting against any billed or quota-tracked identity.

### Likelihood Explanation
No special privileges are required beyond being able to submit a JSON-RPC request that this handler's `Methods()` route to (`MethodSecretsGet`, `MethodCapabilityExec`, see [8](#0-7) ). The exploit only requires generating a new unique request ID per call — trivial and fully repeatable, requiring no valid DON node key, no vault/JWT authorization, and no privileged credential.

### Recommendation
Add caller/owner authentication (equivalent to the vault handler's `Authorizer`/`AuthResult` flow or the HTTP trigger handler's JWT-derived `workflowOwner`) to `HandleJSONRPCUserMessage`, and enforce a per-owner rate limiter/quota (keyed on the verified owner identity, not `req.ID`) before calling `fanOutToNodes`, so quota is checked prior to DON dispatch rather than only diagnostic node-response limiting after the fact.

### Proof of Concept
Go handler-level test plan:
1. Construct a `handler` via `NewHandler` with a mock `DON` and small `donConfig.Members`.
2. Loop N times (N > any reasonable expected per-caller quota), each iteration calling `HandleJSONRPCUserMessage` with a freshly generated `req.ID` (e.g., `uuid.New().String()`) and no `req.Auth`/owner binding, using the same underlying "attacker" identity conceptually.
3. Assert via the mocked `DON.SendToNode` that `fanOutToNodes` is invoked for every one of the N iterations (i.e., `SendToNode` call count == N × len(donConfig.Members)), demonstrating no per-owner ceiling is ever applied.
4. Contrast with `vault/handler_test.go`'s duplicate-request-ID test ( [9](#0-8) ) and `http_trigger_handler_test.go`'s `TestHttpTriggerHandler_HandleUserTriggerRequest_RateLimiting` ( [10](#0-9) ), which show other handlers reject excess per-owner requests — no equivalent test/assertion exists for `confidentialrelay`, confirming the gap.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L341-343)
```go
func (h *handler) Methods() []string {
	return []string{MethodSecretsGet, MethodCapabilityExec}
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L391-406)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		l.Debug("global relay rate limit exceeded")
		return nil
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-652)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
	}

	_ = group.Wait()

	numNodeErrors := nodeErrors.Load()
	remainingPossibleResponses := len(h.donConfig.Members) - int(numNodeErrors)
	if remainingPossibleResponses < h.donConfig.F+1 && numNodeErrors > 0 {
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes")))
	}

	l.Debugw("successfully forwarded request to relay nodes")
	return nil
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L431-446)
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

**File:** core/services/gateway/handlers/confidentialrelay/bundler.go (L98-101)
```go
func (s *BundleSummary) setSignedResponse(resp *jsonrpc.Response[json.RawMessage], signed int) {
	s.response = resp
	s.signed = signed
}
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L1973-2016)
```go
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
```
