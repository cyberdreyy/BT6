Based on my investigation, I found a concrete analog to the reported bug class in the HTTP Trigger Handler's JWT replay-protection cache within the internet-facing Gateway.

### Title
JWT replay-protection cache expiry is decoupled from JWT token validity, allowing replay of still-valid tokens after cache eviction - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
### Finding Description
The Gateway's `WorkflowMetadataHandler.Authorize` function protects against JWT replay by checking a `jwtReplayCache` keyed by the JWT's `jti` claim [1](#0-0) . Entries are recorded on successful authorization with only a `time.Now()` timestamp [2](#0-1)  and are purged later purely based on a configurable `cleanupPeriod`/`cutoff` window, independent of the token's own `exp` claim [3](#0-2) .

This is structurally the same root cause as the `LocalCache#set_expire` bug: the cache's notion of "still valid / not yet expired" is tracked by an internal bookkeeping timestamp that is decoupled from the actual validity window of the underlying credential it is meant to gate. In the original report, `set_expire` could extend/reactivate an entry independent of whether the real data behind it should already be considered gone. Here, the replay-guard's internal TTL (`JWTReplayPeriodMs`, default configurable) [4](#0-3)  is not tied to the actual maximum lifetime of a JWT, which is separately capped at `maxJWTExpiryDuration = 5 * time.Minute` in the JWT verification code [5](#0-4) . `VerifyRequestJWT` only enforces that `exp - iat <= maxJWTExpiryDuration`; it does not compare `JWTReplayPeriodMs` against `exp` [6](#0-5) .

If `JWTReplayPeriodMs` is configured to a value shorter than the actual `exp - iat` window a client chooses (up to the 5-minute ceiling), the `jti` entry can be evicted from `jwtReplayCache` by `cleanupOldEntries` while the JWT itself is still cryptographically valid and unexpired per its own `exp` claim. A client (or anyone who captured the token/request) could then resubmit the exact same signed JWT + request body and pass `Authorize` a second time, because `isReplay` only checks presence in the map, not whether the token itself should still be tracked [7](#0-6) . This defeats the replay-protection guarantee described directly in the config comment: "JWTReplayPeriodMs is how long JWT IDs are cached to prevent replay attacks" [4](#0-3) .

### Impact Explanation
`Authorize` is the gate that allows an inbound HTTP trigger request to run against `WorkflowMetadataHandler`'s authorized keys and ultimately dispatch a workflow execution to nodes [8](#0-7) . The existing tests confirm replay protection is treated as a security control (duplicate JWT is explicitly rejected) [9](#0-8) . If the replay window is misconfigured shorter than the maximum JWT lifetime, an unprivileged actor who observed or replayed a captured, still-valid signed request can re-trigger the same workflow execution — an unauthorized/duplicate job run, which matches the "unauthorized job run" acceptance criterion.

### Likelihood Explanation
This requires `JWTReplayPeriodMs` to be operator-configured below the maximum possible JWT validity window (`exp - iat` up to 5 minutes, per `maxJWTExpiryDuration`). There is no code-level validation enforcing `JWTReplayPeriodMs >= maxJWTExpiryDuration`, so this is a plausible misconfiguration rather than a guaranteed-exploitable default; I was not able to confirm the actual default value of `JWTReplayPeriodMs` used in production configs within the indexed context, which limits certainty about real-world exploitability.

### Recommendation
Enforce `JWTReplayPeriodMs >= maxJWTExpiryDuration` at config validation time (or simply derive the replay-cache retention window from each token's own `exp` claim rather than a fixed, independently configured period), so that a `jti` can never be evicted from the replay guard while its corresponding JWT is still valid.

### Proof of Concept
1. Configure the Gateway's HTTP handler with `JWTReplayPeriodMs` set below 300000 (5 minutes), e.g. 60000 (1 minute).
2. Craft a JWT with `iat = now`, `exp = now + 5m` (maximum allowed by `maxJWTExpiryDuration`) and a valid signature/digest for a `MethodWorkflowExecute` request.
3. Submit the request; `Authorize` succeeds and `recordUsage(jti)` stores `jti -> now`.
4. Wait slightly over 1 minute (the configured `JWTReplayPeriodMs`) — `cleanupOldEntries` purges the `jti` entry from `jwtReplayCache`, while the JWT's own `exp` (4 more minutes remaining) is still valid.
5. Resubmit the identical signed JWT + request. `VerifyRequestJWT` still validates it (not expired), and `isReplay(jti)` now returns `false` because the entry was purged — `Authorize` succeeds a second time, re-triggering the workflow execution.

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-405)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L407-412)
```go
func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L414-426)
```go
// cleanupOldEntries removes expired entries from the cache
func (cache *jwtReplayCache) cleanupOldEntries(cutoff time.Time) int {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	var expiredCount int
	for jti, createdAt := range cache.cache {
		if createdAt.Before(cutoff) {
			delete(cache.cache, jti)
			expiredCount++
		}
	}
	return expiredCount
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L105-106)
```go
	// JWTReplayPeriodMs is how long JWT IDs are cached to prevent replay attacks (in milliseconds)
	JWTReplayPeriodMs int `json:"jwtReplayPeriodMs"`
```

**File:** core/utils/jwt.go (L19-22)
```go
const (
	maxJWTExpiryDuration     = 5 * time.Minute // Maximum allowed expiry duration
	defaultIssuedAtTolerance = 5 * time.Minute // Default tolerance for issuedAt validation to handle clock drift
)
```

**File:** core/utils/jwt.go (L290-298)
```go
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L380-393)
```go
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
```
