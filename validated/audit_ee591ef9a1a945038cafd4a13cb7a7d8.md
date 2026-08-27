### Title
JWT Replay Guard Check-Then-Act Race Allows Duplicate/Sandwiched Workflow Execution - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
The `jwtReplayCache` used to prevent replay of signed HTTP-trigger requests performs its "is this token already used" check and its "record this token as used" write as two separate, non-atomic locked operations. An unprivileged, external caller of the internet-facing HTTP Trigger gateway can exploit this gap to have the same signed JWT accepted twice concurrently, defeating the single-use replay protection — the same class of check-then-act/TOCTOU flaw described in the sandwich-attack report (attacker exploits the window between an old and new state to get double value from a single authorization).

### Finding Description
`WorkflowMetadataHandler.Authorize` is the authorization gate for every inbound `workflows.execute` HTTP trigger request coming from an external, unauthenticated-by-session caller through the gateway. It performs: [1](#0-0) 

```
if h.jwtCache.isReplay(claims.ID) { ... return err }
... (authorized-keys lookup) ...
h.jwtCache.recordUsage(claims.ID)
```

`isReplay` takes an `RLock` on the cache and returns, releasing the lock; `recordUsage` is invoked afterward and separately acquires the write `Lock`: [2](#0-1) 

There is no single atomic "check-and-set" operation (unlike the vault package's `RequestReplayGuard.CheckAndRecord`, which correctly holds one mutex across both the check and the write): [3](#0-2) 

Because `Authorize` is called from `httpTriggerHandler.authorizeRequest` per inbound HTTP request without any per-JTI serialization upstream, two requests carrying the identical signed JWT sent concurrently by the same external caller can both pass the `isReplay` check before either calls `recordUsage`, since the two calls are not mutually exclusive with the check. [4](#0-3) 

This mirrors the report's bug class exactly: a narrow window between reading old state (exchange rate / "not yet replayed") and committing new state (updated rate / "now replayed") that an attacker can straddle with two nearly-simultaneous requests to extract value twice from a mechanism meant to be single-use.

### Impact Explanation
Successful exploitation lets an external, unprivileged caller trigger duplicate execution of a workflow using a single authorized JWT that was meant to authorize exactly one execution. Depending on what the workflow does (e.g. triggering on-chain fund transfers, external API side effects, or paid downstream automation), this can cause double/duplicate job runs and consequently duplicated financial or state-changing side effects, corresponding to the "unauthorized job run" impact category called out in scope. It also undermines a stated security control (`errors.New("JWT token has already been used...")`), which is a genuine authentication/replay-protection bypass.

### Likelihood Explanation
Likelihood is moderate: the attacker needs to fire the same signed JWT-bearing request twice within a very short time window (network race), which is straightforward for any external client capable of issuing concurrent HTTP requests to the gateway (no special privilege required — this is exactly the "sandwich" pattern of racing two client-controlled calls around a single mutable state transition). The race window is narrow (two lock acquisitions with computation for the authorized-key lookup in between), which raises the bar slightly but is a well-known and reliably exploitable class of race in production systems, especially with request replication across multiple gateway/DON paths.

### Recommendation
Make the replay check-and-record atomic, following the pattern already used in `core/capabilities/vault/request_replay_guard.go`. Specifically, in `jwtReplayCache`, add a single method that holds one write lock across both the "already seen" check and the insertion (e.g. `CheckAndRecord(jti) error`), and use it in place of the separate `isReplay` + `recordUsage` calls in `WorkflowMetadataHandler.Authorize`. This closes the TOCTOU gap and ensures a given `jti` can only ever pass authorization once, regardless of concurrency.

### Proof of Concept
1. An external, unprivileged caller creates one valid signed JWT for a `workflows.execute` HTTP trigger request as normal (per `TestWorkflowMetadataHandler_Authorize`'s "JWT replay protection" pattern): [5](#0-4) 
2. Instead of sending the request once, the caller fires two concurrent HTTP requests carrying the identical `req.Auth` JWT token to the gateway's HTTP Trigger endpoint.
3. Both requests reach `httpTriggerHandler.authorizeRequest` → `WorkflowMetadataHandler.Authorize` concurrently.
4. If both goroutines execute `h.jwtCache.isReplay(claims.ID)` before either executes `h.jwtCache.recordUsage(claims.ID)`, both requests pass authorization and are forwarded to `sendWithRetries`, causing the workflow to be triggered twice from a single authorized JWT — the replay guard's protection is bypassed.

Note: I could not execute this race locally to empirically confirm timing reliability (e.g., under `-race`/stress testing) because I only have read access to the indexed codebase; a background Devin session with the full repository and test tooling would be needed to write a concurrency test (`go test -race`) that reliably reproduces the double-authorization window and to verify the fix.

### Citations

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

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
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
