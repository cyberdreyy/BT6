### Title
JWT Replay-Protection Window Can Expire Before Token's Own `exp`, Allowing HTTP Trigger Request Replay - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
The gateway's JWT-based replay protection for HTTP trigger requests evicts used JWT IDs (`jti`) from its in-memory cache based on a fixed `cleanupPeriod` timer that is independent of the JWT's own `exp` claim. Because a JWT is considered valid by `jwt.ParseWithClaims` purely based on its `exp`/`iat` claims (up to `maxJWTExpiryDuration` = 5 minutes), a used `jti` can be purged from the replay cache while the token itself is still cryptographically valid, allowing the exact same signed request to be replayed and processed a second time.

### Finding Description
`Authorize` checks `h.jwtCache.isReplay(claims.ID)` before accepting a request, and calls `h.jwtCache.recordUsage(claims.ID)` only after successful authorization: [1](#0-0) 

The replay cache is purged periodically by `cleanupOldEntries`, using a cutoff computed as `now - cleanupPeriod`, i.e. entries are evicted once older than one `cleanupPeriod` window — this window is `JWTReplayPeriodMs`, a value fully decoupled from the token's own lifetime: [2](#0-1) [3](#0-2) 

Meanwhile, `VerifyRequestJWT` independently validates the token using only its `iat`/`exp` claims, permitting a maximum lifetime of `maxJWTExpiryDuration` (5 minutes) that has no relationship to `JWTReplayPeriodMs`: [4](#0-3) [5](#0-4) 

Because these two time windows (replay-cache retention vs. JWT expiry) are configured independently, if `JWTReplayPeriodMs` is set smaller than a token's actual lifetime (up to 5 minutes), the `jti` record for an already-used token can be evicted from the cache while the token remains within its `exp` window. A subsequent submission of the identical signed JSON-RPC request (same `digest`, same `jti`, same signature) will pass `isReplay` (no longer present in cache) and pass `VerifyRequestJWT` (still within `exp`), resulting in the request being authorized and processed a second time. This is directly analogous to the `TimeLockPool.extendLock` bug: an incorrect/independent time-window computation (recomputing from "now" rather than accounting for the full elapsed lifetime) causes an authoritative check (there: reward accrual; here: replay protection) to silently produce an incorrect result for part of the intended duration.

### Impact Explanation
A successfully replayed HTTP trigger JWT causes the gateway to re-authorize and re-dispatch the exact same workflow-triggering request to the DON, resulting in unauthorized duplicate job execution. This maps to the "unauthorized job run" acceptance criterion — a network observer or a party the request passed through (proxy, log, browser history, etc.) could resubmit the intercepted request within the exp window after it drops out of the local replay cache, triggering a job run without new authorization from the legitimate signer.

### Likelihood Explanation
Exploitation requires: (a) `JWTReplayPeriodMs` configured smaller than the effective token lifetime used by clients (up to `maxJWTExpiryDuration` = 5 minutes), and (b) the attacker capturing a previously-submitted valid signed request. The relevant default value of `JWTReplayPeriodMs` (`defaultJWTReplayPeriodMs`) could not be confirmed from the available index content, so whether default configuration is affected is uncertain; likelihood is dependent on deployment configuration.

### Recommendation
Tie replay-cache retention to the token's actual `exp` claim (retain each `jti` at least until its `exp` has passed, e.g., store `exp` alongside `jti` and only evict once `now > exp`), rather than using a fixed, independently-configured `cleanupPeriod`/`JWTReplayPeriodMs`. Alternatively, enforce `JWTReplayPeriodMs >= maxJWTExpiryDuration` at configuration validation time.

### Proof of Concept
1. Configure gateway with `JWTReplayPeriodMs` < 5 minutes (the max allowed JWT lifetime).
2. Client signs and submits a valid HTTP trigger JWT-authenticated request with `exp = iat + 5m`.
3. Gateway processes and records `jti` in `jwtCache` via `recordUsage`.
4. After `JWTReplayPeriodMs` elapses (but before the token's `exp`), the cleanup ticker evicts the `jti` entry via `cleanupOldEntries`.
5. Attacker (or original client) resubmits the identical signed request; `isReplay` returns `false` (jti no longer cached) and `VerifyRequestJWT` still succeeds (token not yet expired), so the request is authorized and processed again.

Note: I was unable to verify the exact numeric value of `defaultJWTReplayPeriodMs` within the indexed content, so confirmation that default configuration is vulnerable (vs. only certain custom configurations) is uncertain.

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L298-304)
```go
		h.runTicker(h.jwtCache.cleanupPeriod, func(ctx context.Context) {
			now := time.Now()
			expiredCount := h.jwtCache.cleanupOldEntries(now.Add(-h.jwtCache.cleanupPeriod))
			h.metrics.IncrementJwtCacheCleanUpCount(ctx, int64(expiredCount), h.lggr)
			h.metrics.RecordJwtCacheSize(ctx, int64(len(h.jwtCache.cache)), h.lggr)
			h.lggr.Debugw("Workflow execution cache cleanup completed", "expired_entries", expiredCount, "remaining_entries", len(h.jwtCache.cache))
		})
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
