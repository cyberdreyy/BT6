### Title
Check-then-act race condition in `jwtReplayCache` allows single JWT to authorize a workflow execution more than once - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` calls `jwtCache.isReplay(claims.ID)` and, only after passing all other checks, calls `jwtCache.recordUsage(claims.ID)` to mark the `jti` as used. These are two independently-locked operations rather than one atomic check-and-set, so two concurrent `Authorize` calls carrying the same JWT (same `jti`) can both observe "not replayed" before either commits, defeating the single-use replay guarantee.

### Finding Description
`Authorize` at [1](#0-0)  performs, in order: JWT signature/digest verification, `h.jwtCache.isReplay(claims.ID)`, authorized-key lookup, and finally `h.jwtCache.recordUsage(claims.ID)`. The cache primitives are implemented as separate lock scopes: [2](#0-1)  — `isReplay` takes an `RLock`/`RUnlock` and returns, and `recordUsage` separately takes `Lock`/`Unlock`. There is no single critical section covering "check-not-used, then mark-used"; the two calls happen non-atomically with unrelated other work (the authorized-key map lookup) interleaved between them.

This is reachable directly from the gateway's HTTP trigger path: `httpTriggerHandler.authorizeRequest` invokes `h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)` for every inbound `HandleUserTriggerRequest` call [3](#0-2) , with `req.Auth` fully attacker-controlled (a self-signed JWT any external caller can construct, since the signing key is simply an ECDSA key the caller controls). An unauthenticated/unprivileged client that already possesses one valid, correctly-signed JWT for a workflow it is authorized to call can fire two (or more) concurrent `HandleUserTriggerRequest` calls carrying that identical token. Both goroutines reach `Authorize` concurrently; both can execute `isReplay(claims.ID)` and see `false` before either has called `recordUsage(claims.ID)`, because the RWMutex only protects each individual map access, not the compound "check-then-set" invariant. Both authorizations then succeed and are dispatched to the DON, resulting in two accepted, distinct trigger executions from a single JWT — the exact scenario the existing sequential test at `TestHttpTriggerHandler_HandleUserTriggerRequest` "duplicate JWT token and request ID" [4](#0-3)  and `TestWorkflowMetadataHandler_Authorize` "JWT replay protection" [5](#0-4)  assume is prevented — but neither test exercises the concurrent path, so the race is untested and unmitigated.

Regarding the cross-workflow angle raised in the question: because `jwtCache` is a single global map keyed only by `jti` [6](#0-5) , and `jti` is an attacker-supplied claim value inside a token the attacker signs themselves (not derived from or bound to the workflow ID or digest — only `Digest` is cryptographically bound, per `VerifyRequestJWT` [7](#0-6) ), an attacker who controls signing keys authorized on two different workflows could deliberately choose the same `jti` for two crafted tokens targeting workflow A and workflow B, and race them the same way to double up. However, the minimal, directly reachable exploit does not require any cross-workflow setup at all — replaying the exact same token/workflow pair concurrently is sufficient and is the core root cause.

### Impact Explanation
This breaks the single-use / anti-replay invariant the gateway explicitly implements for HTTP-triggered workflow executions, allowing an unprivileged caller with one valid signed request to cause duplicate downstream workflow executions ("unauthorized job run" / double execution) purely via request timing, with no signature forgery or credential theft needed. Depending on what the triggered workflow does (e.g., triggers a payment, mints a token, calls an external side-effecting API), this can translate into double execution of state-changing effects that the system's replay protection is meant to make exactly-once.

### Likelihood Explanation
The only precondition is possession of one valid JWT for a workflow the attacker is legitimately authorized to trigger (which is a normal capability, not an elevated one) and the ability to send two requests to the gateway close together in time — both are within reach of any external API/gateway client. The race window is small (between an `RLock`-protected read and a later `Lock`-protected write, with other logic executing in between), but is real, deterministic to construct, and can be made reliable with straightforward test/PoC techniques (e.g., synchronization barriers/goroutine start signals) rather than requiring rare timing luck in production traffic.

### Recommendation
Make the replay check-and-record atomic: hold a single mutex for the full "check `isReplay`, then `recordUsage`" sequence (e.g., a `CheckAndRecord(jti string) bool` method on `jwtReplayCache` that takes the write lock once and does both the existence check and the insert under one critical section, returning whether the token was already used), and call that from `Authorize` instead of the separate `isReplay`/`recordUsage` calls.

### Proof of Concept
Go concurrency test plan in `workflow_metadata_handler_test.go`:
1. Build one signed JWT for a workflow with a registered authorized key (as in `TestWorkflowMetadataHandler_Authorize`).
2. Launch two goroutines concurrently, both calling `handler.Authorize(workflowID, tokenString, req)` with the identical token/`req`, synchronized to start together via a `sync.WaitGroup`/channel barrier to maximize overlap of the `isReplay`→`recordUsage` window (optionally run with `-race` and loop N times or inject a small `time.Sleep`/hook between `isReplay` and `recordUsage` to widen the window deterministically).
3. Collect both results; assert that in the current implementation, both calls can return `(key, nil)` (no error) at least once across repeated runs, demonstrating the replay cache failed to reject the second concurrent use — contrary to the sequential test's expectation that a second use always fails with "JWT token has already been used."
4. After applying the atomic `CheckAndRecord` fix, re-run the same concurrent test and assert exactly one goroutine succeeds and the other receives the "already been used" error deterministically.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L29-34)
```go
// jwtReplayCache manages used JWT IDs to prevent replay attacks
type jwtReplayCache struct {
	mu            sync.RWMutex
	cleanupPeriod time.Duration
	cache         map[string]time.Time // jti -> timestamp
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-108)
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
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-412)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-369)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L357-394)
```go
	t.Run("duplicate JWT token and request ID", func(t *testing.T) {
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
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "token has already been used")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrInvalidRequest)
	})
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go (L1193-1217)
```go
	t.Run("JWT replay protection", func(t *testing.T) {
		params := json.RawMessage(`{"test": "data"}`)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-replay",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &params,
		}

		token, err := utils.CreateRequestJWT(*req)
		require.NoError(t, err)

		tokenString, err := token.SignedString(privateKey)
		require.NoError(t, err)

		key, err := handler.Authorize(workflowID, tokenString, req)
		require.NoError(t, err)
		require.NotNil(t, key)

		// Second authorization with same JWT should fail (replay attack)
		key, err = handler.Authorize(workflowID, tokenString, req)
		require.Error(t, err)
		require.Contains(t, err.Error(), "JWT token has already been used. Please generate a new one with new id (jti)")
		require.Nil(t, key)
	})
```

**File:** core/utils/jwt.go (L277-301)
```go
	reqDigest, err := req.Digest()
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	if verifiedClaims.ID == "" {
		return nil, gethcommon.Address{}, errors.New("JWT ID (jti) is required but missing")
	}
	if verifiedClaims.ExpiresAt == nil {
		return nil, gethcommon.Address{}, errors.New("expiredAt (exp) is required but missing")
	}
	if verifiedClaims.IssuedAt == nil {
		return nil, gethcommon.Address{}, errors.New("issuedAt (iat) is required but missing")
	}
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
	if verifiedClaims.Digest != "0x"+reqDigest {
		return nil, gethcommon.Address{}, fmt.Errorf("claim digest '%s' does not match calculated request digest '0x%s'", verifiedClaims.Digest, reqDigest)
	}
```
