## Title
JWT replay-cache check-then-act race in `WorkflowMetadataHandler.Authorize` allows a single-use JWT to authorize multiple concurrent workflow trigger requests - (File: `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go`)

### Summary
The Insure bug used `==` instead of `=` so the `insurances[_id].status` flag was checked, never mutated, allowing a single unlock request to be repeated indefinitely because the "already used" state was never durably recorded before the next use could pass the check. The Chainlink gateway's JWT single-use enforcement for HTTP-triggered workflow execution has the same fundamental defect in spirit: the "has this token been used" check and the "mark token as used" mutation are split into two separately-locked operations with unrelated work executed in between, so concurrent callers can both observe "not yet used" before either one records usage.

### Finding Description
`WorkflowMetadataHandler.Authorize` is the unprivileged-facing entry point that authenticates an HTTP trigger request via a per-request JWT (`jti` claim) before it is allowed to route a request to a workflow's DON: [1](#0-0) 

The check (`h.jwtCache.isReplay(claims.ID)`) and the mutation (`h.jwtCache.recordUsage(claims.ID)`) are implemented as two independent, separately-locked methods on `jwtReplayCache`: [2](#0-1) 

`isReplay` takes an `RLock`, checks map membership, and releases the lock; `recordUsage` is only called at the very end of `Authorize`, after the authorized-keys map lookups (`h.authorizedKeys[workflowID]`) have completed — work that is not trivial and provides a window for another goroutine to run. Because the read (`isReplay`) and the write (`recordUsage`) are not performed atomically under a single critical section, two (or more) concurrent `HandleUserTriggerRequest` calls presenting the identical signed JWT can both pass the `isReplay` check before either calls `recordUsage`, resulting in both being authorized.

This is directly analogous to the reported bug class: a state flag intended to prevent re-use of a single credential/authorization is not reliably persisted/checked atomically, so the "already used" guarantee is silently broken and the same authorization artifact can be consumed more than once.

### Impact Explanation
JWT single-use enforcement exists specifically to stop a captured/observed request (e.g., replayed by a network observer, or resubmitted by a malicious or buggy client) from being used to trigger the same workflow execution multiple times. If the race is won, an unprivileged caller in possession of one valid signed JWT can cause more than one workflow execution/trigger dispatch to the DON shards using a token that was supposed to be single-use, defeating the intended replay protection and resulting in duplicate/unauthorized job execution (`sendWithRetries` → dispatch to shards) for a token the security model assumes is consumed exactly once. This is a concrete authorization/replay-protection bypass on the internet-facing HTTP trigger gateway path.

### Likelihood Explanation
Exploitation requires only submitting the same valid JWT concurrently (e.g., two near-simultaneous HTTP requests), which is easy to reproduce for a client that has legitimately obtained one JWT and wants to trigger extra executions, or for a token replayed by an eavesdropper racing the legitimate sender. No privileged access is needed — the caller only needs one previously-valid signed JWT and workflowID, both of which flow through the standard unprivileged HTTP trigger path in `httpTriggerHandler.HandleUserTriggerRequest` → `authorizeRequest` → `WorkflowMetadataHandler.Authorize`.

### Recommendation
Make the check-and-record operation atomic: acquire a single lock covering both the `isReplay` check and the `recordUsage` write (i.e., collapse them into one method, e.g. `checkAndRecord(jti) (alreadyUsed bool)` that holds `cache.mu.Lock()` for the entire read-then-insert sequence), and call it immediately once the JWT signature is verified, before performing the (non-trivial) authorized-key lookup, so the anti-replay guarantee is not exposed to a TOCTOU window.

### Proof of Concept
1. Register a workflow and obtain one valid signed JWT for a `workflows.execute` request (as in `TestHttpTriggerHandler_HandleUserTriggerRequest`'s "duplicate JWT token and request ID" test), with `req.Auth` set to the JWT.
2. Fire two concurrent goroutines both calling `handler.HandleUserTriggerRequest(ctx, req, callback, time.Now())` with the identical `req` (same `jti`).
3. Because `isReplay` and `recordUsage` in `jwtReplayCache` (`core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go:399-412`) are not executed under one atomic critical section within `Authorize` (`workflow_metadata_handler.go:80-108`), both goroutines can observe `isReplay(jti) == false` before either calls `recordUsage(jti)`, causing both calls to succeed and dispatch the trigger twice instead of the second one being rejected with "JWT token has already been used."

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
