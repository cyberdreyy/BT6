### Title
JWT replay cache TOCTOU race allows double-execution of a single-use signed trigger JWT - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks `h.jwtCache.isReplay(claims.ID)` and later calls `h.jwtCache.recordUsage(claims.ID)` as two separate lock acquisitions, with authorization key lookups occurring in between. This creates a time-of-check-to-time-of-use (TOCTOU) window where two concurrent `Authorize` calls with the identical signed JWT can both observe `isReplay == false` and both pass authorization before either records the JTI as used.

### Finding Description
`Authorize` in [1](#0-0)  performs: verify JWT signature/claims, check `h.jwtCache.isReplay(claims.ID)` (takes `RLock`, checks map, releases lock), then performs authorized-key lookups, then finally calls `h.jwtCache.recordUsage(claims.ID)` (separate `Lock` acquisition) only at the very end. The `isReplay` and `recordUsage` methods are implemented as independent critical sections: [2](#0-1) . There is no single atomic "check-and-set" operation (e.g., a compare-and-swap on the map), so the read (`isReplay`) and the write (`recordUsage`) are not synchronized against each other for the same key across concurrent calls. If an attacker (a valid, single-use signer JWT holder — an unprivileged but legitimately authorized workflow signer per the intended one-shot design) fires two (or more) requests carrying the same signed JWT concurrently, both goroutines can pass the `isReplay` check (both see the JTI absent from the cache) before either goroutine reaches `recordUsage`. Both would then pass the authorized-key check with the same valid signer and both would return a valid `*gateway.AuthorizedKey`, resulting in double execution of the workflow trigger from a single signed, intended-single-use JWT.

This is reachable purely from attacker-controlled HTTP/gateway trigger requests carrying a previously-obtained valid JWT — no operator/host access is required, and it bypasses the intended authentication invariant that a signed trigger JWT authorizes exactly one execution.

### Impact Explanation
This breaks authentication soundness for the JWT-based single-use trigger authorization mechanism, permitting replay of a single authorized workflow trigger request. Concretely, this maps to the "unauthorized job run" / duplicate action impact class: an attacker with one valid signed JWT can cause the associated workflow trigger to execute more than once, which is a real security-relevant duplication/replay of an authenticated action (e.g., double-firing on-chain actions, double billing, or double side-effects depending on what the trigger drives).

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to possess one valid, correctly signed JWT that would normally be accepted once (this is exactly the credential level intended for a single legitimate signer/trigger call — no elevated privileges required). The race window is small (the time between the `isReplay` RLock release and the eventual `Lock` in `recordUsage`, including a map lookup and workflow ID/key check) but is trivially reproducible by firing concurrent requests, and repeatable on every distinct signed JWT the attacker obtains. Concurrency-based bypass is a standard, reliable exploitation technique (no timing luck required beyond firing simultaneous requests), so likelihood is high given the precondition is met.

### Recommendation
Merge the check and the record into a single atomic operation under one lock acquisition, e.g., add a `checkAndRecord(jti string) bool` method on `jwtReplayCache` that takes `mu.Lock()` once, checks presence, and if absent immediately inserts before releasing the lock — returning whether the JTI was already used. Call this atomic method in `Authorize` in place of the separate `isReplay`/`recordUsage` calls (marking the JTI used before performing the authorized-key checks, or using a "reserve-then-confirm" pattern with rollback on later failure) so that no two concurrent calls can observe "not yet used" simultaneously.

### Proof of Concept
Go concurrency test plan:
```go
func TestAuthorize_ConcurrentReplaySameJWT(t *testing.T) {
    h := NewWorkflowMetadataHandler(...)
    // seed h.authorizedKeys[workflowID] with the test signer's key
    token := generateValidJWT(signerKey, jti)

    const n = 20
    var wg sync.WaitGroup
    successCount := int32(0)
    for i := 0; i < n; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            _, err := h.Authorize(workflowID, token, req)
            if err == nil {
                atomic.AddInt32(&successCount, 1)
            }
        }()
    }
    wg.Wait()
    require.Equal(t, int32(1), successCount, "exactly one Authorize call should succeed for a single-use JWT")
}
```
Expected (buggy) result: `successCount > 1` under race, demonstrating that `isReplay`/`recordUsage` non-atomicity allows duplicate acceptance of the same JWT. Run with `-race` to also confirm no data race on the map access itself, separate from the logical TOCTOU issue.

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
