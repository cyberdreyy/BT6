### Title
Non-atomic check-then-act race between `jwtReplayCache.isReplay` and `recordUsage` allows single JWT to authorize twice, including against two different workflow IDs - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `jti` replay status and records `jti` usage using two separate, non-atomic lock acquisitions (`isReplay` then, after the authorizedKeys check, `recordUsage`), with no lock held across the whole `Authorize` call. A signer holding a single valid JWT who is authorized for two different `workflowID`s can invoke `Authorize` concurrently with the same token for both workflow IDs and pass the replay check both times before either goroutine records the `jti`, resulting in a single JWT/jti being accepted twice.

### Finding Description
`Authorize` first calls `utils.VerifyRequestJWT` (which validates signature, digest-binding to the JSON-RPC request content, expiry, etc. — but the `JWTClaims` struct contains no `workflowID` field at all [1](#0-0) ), then checks `h.jwtCache.isReplay(claims.ID)` [2](#0-1) , then checks whether the signer is in the per-workflow `authorizedKeys` set for the caller-supplied `workflowID`, and only afterwards calls `h.jwtCache.recordUsage(claims.ID)` [3](#0-2) .

`isReplay` and `recordUsage` acquire the cache's mutex independently and release it immediately after the check/write [4](#0-3) ; there is no lock held for the duration of `Authorize`. Because "binding" of a JWT to a workflow is enforced purely by checking `signer ∈ authorizedKeys[workflowID]` and not by any workflow claim inside the JWT itself, a signer that is legitimately authorized for two workflows (A and B) can submit the *same* JWT concurrently as `Authorize(A, token, req)` and `Authorize(B, token, req)`. Both calls can pass `isReplay(jti)==false` before either call reaches `recordUsage(jti)`, so both proceed to their respective (successful) `authorizedKeys` checks and both return a valid `*gateway.AuthorizedKey`. This breaks the single-use invariant of `jti` and allows one signed credential to authorize two separate accepted requests instead of one.

### Impact Explanation
This allows a legitimate holder of a signer key authorized on multiple workflows to double-spend a single-use JWT, e.g., triggering the same (or replayed) workflow-execution request twice against two different workflow IDs, or twice against the same workflow, when the intended design is exactly-once acceptance per `jti`. This maps to a request-impersonation / replay-protection-bypass class issue (unauthorized duplicate action / allowlist-quota bypass), rather than full cross-tenant compromise, since the attacker must already be a validly authorized signer for both target workflows.

### Likelihood Explanation
Requires: (1) a signer key legitimately authorized for at least one workflow (or two, to hit the cross-workflow variant), and (2) the ability to fire two `Authorize` calls with the identical token concurrently (trivial for an attacker sending two near-simultaneous HTTP/gateway requests) so both threads race through `isReplay` before either calls `recordUsage`. This is a narrow timing window but fully attacker-controllable (attacker chooses submission timing) and repeatable — no operator/admin access needed, only a valid signer credential the attacker already controls.

### Recommendation
Make the check-and-mark atomic: hold `jwtCache.mu` (a single `Lock`) across both the replay check and the recordUsage write, e.g., add a combined `checkAndRecord(jti) bool` method that performs `if _, exists := cache[jti]; exists { return false }; cache[jti] = time.Now(); return true` under one write lock, and call it once in `Authorize` immediately after JWT verification, before doing the `authorizedKeys` lookup. Additionally, consider whether JWTs should carry an explicit workflow-binding claim if the intended security model is per-workflow single-use tokens.

### Proof of Concept
Go test plan (`workflow_metadata_handler_test.go`):
1. Create a handler with `authorizedKeys` containing the same signer key for both `workflowIDA` and `workflowIDB`.
2. Create one JWT (single `jti`) signed by that key for a fixed `req`.
3. Launch two goroutines calling `handler.Authorize(workflowIDA, tokenString, req)` and `handler.Authorize(workflowIDB, tokenString, req)` concurrently, synchronized via a barrier so both call `isReplay` before either calls `recordUsage` (can be forced deterministically by instrumenting/mocking `jwtCache` with a small sleep between the `isReplay` and `recordUsage` calls in a test-only wrapper, or by running many iterations under `-race` to observe both succeeding).
4. Assert that in some run both calls return `(key, nil)` with no error — i.e., the same `jti` is accepted twice — demonstrating the TOCTOU race and violation of single-use `jti` semantics.

### Citations

**File:** core/utils/jwt.go (L133-136)
```go
type JWTClaims struct {
	Digest string `json:"digest"`
	jwt.RegisteredClaims
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L87-90)
```go
	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L92-107)
```go
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
