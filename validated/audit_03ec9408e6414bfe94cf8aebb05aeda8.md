### Title
Vault allowlist authorization uses a stale, expiry-only local cache that ignores on-chain revocation, permitting continued authorized execution of a revoked request until its original expiry - ([File: core/services/workflows/syncer/v2/workflow_registry.go])

### Summary
`workflowRegistry.syncAllowlistedRequests` maintains an in-memory cache (`w.allowListedRequests`) that is only pruned by comparing each entry's `ExpiryTimestamp` against wall-clock time; new entries are appended by polling only the index range beyond `w.lastSeenAllowlistedRequestsCount`. There is no mechanism that re-validates already-cached entries against the contract's current allowlist state, so an owner-initiated revocation of a not-yet-expired `RequestDigest` is invisible to the node until the locally recorded `ExpiryTimestamp` naturally lapses. `AuthorizeRequest` in `allow_list_based_auth.go` only checks membership in this stale cache plus the same `ExpiryTimestamp`, so a revoked-but-unexpired digest continues to authorize vault requests.

### Finding Description
The sync loop in `syncAllowlistedRequests` (`core/services/workflows/syncer/v2/workflow_registry.go:764-804`) prunes `w.allowListedRequests` using only:
```go
for _, request := range w.allowListedRequests {
    if int64(request.ExpiryTimestamp) > time.Now().Unix() {
        activeAllowlistedRequests = append(activeAllowlistedRequests, request)
    } else {
        expiredRequestsCount++
    }
}
``` [1](#0-0) 

New entries are then fetched only for the index range between `w.lastSeenAllowlistedRequestsCount` and the contract's current `totalAllowlistedRequests`, via `getAllowlistedRequests`:
```go
if w.lastSeenAllowlistedRequestsCount.Cmp(totalAllowlistedRequestsResult) == 0 {
    return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, totalAllowlistedRequestsResult, headAtLastRead, nil
}
...
if startIndex.Cmp(w.lastSeenAllowlistedRequestsCount) < 0 {
    startIndex = w.lastSeenAllowlistedRequestsCount
}
``` [2](#0-1) 

Because entries already fetched into `w.allowListedRequests` are never re-queried against the contract, a revocation of an already-cached, unexpired `RequestDigest` (which does not change `totalAllowlistedRequests` in a way the node interprets as "remove this specific entry") is never reflected in the node's local state.

`AuthorizeRequest` in `core/capabilities/vault/allow_list_based_auth.go` relies exclusively on this cache and its own `ExpiryTimestamp` check:
```go
if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
    ...
    return nil, errors.New("request authorization expired")
}
``` [3](#0-2) 

`fetchAllowlistedItem` performs a simple digest match against the cache with no live/authoritative on-chain re-check:
```go
func (r *allowListBasedAuth) fetchAllowlistedItem(allowListedRequests []...) *... {
    for _, item := range allowListedRequests {
        if item.RequestDigest == digest {
            return &item
        }
    }
    return nil
}
``` [4](#0-3) 

Exploit flow: an attacker (any unauthenticated party able to send a signed gateway/vault request whose `RequestDigest` was legitimately allowlisted by the true owner) has that allowlist entry revoked by the owner before expiry. Because the node's cache is append-only and only time-pruned, the attacker can submit that exact request to the Vault gateway and it will still pass `AuthorizeRequest` until the original `ExpiryTimestamp` elapses, regardless of the owner's revocation.

### Impact Explanation
This is an allowlist/authorization bypass: a request that the true workflow owner has explicitly revoked continues to be treated as authorized by the Vault DON, enabling unauthorized execution (e.g., secret access/vault operation) at the owner's expense for the remainder of the original expiry window. This matches Chainlink's "unauthorized job run" / "allowlist bypass" impact class since authorization no longer reflects current on-chain state as required by the intended invariant.

### Likelihood Explanation
Requires only that: (1) the attacker previously had a request digest legitimately allowlisted, (2) the true owner revokes it without waiting for natural expiry, and (3) the attacker replays the same request before the original `ExpiryTimestamp`. No special privileges beyond being a normal, previously-authorized requester (or someone who can reconstruct the exact digest) are needed, and the flaw is deterministic/repeatable — it does not depend on race timing beyond “before the cached expiry elapses,” which can be arbitrarily long relative to the revocation.

### Recommendation
Add revocation-awareness to the sync logic: either (a) have the contract/reader expose revoked digests explicitly and remove them from `w.allowListedRequests` on the next sync tick, or (b) re-validate each cached entry's continued active status against the contract (not just relying on `ExpiryTimestamp`) on every sync cycle rather than only appending new entries beyond `lastSeenAllowlistedRequestsCount`.

### Proof of Concept
Go unit test plan for `core/capabilities/vault/allow_list_based_auth_test.go`:
1. Construct a fake `WorkflowRegistrySyncer` whose `GetAllowlistedRequests` returns a single entry with `RequestDigest = D`, `ExpiryTimestamp = now + 1 hour`, simulating a revoked-but-not-expired cached entry (i.e., the on-chain state has been revoked but the node's stale cache still contains it, per the syncer's expiry-only pruning behavior demonstrated above).
2. Build a `jsonrpc.Request` whose digest equals `D`.
3. Call `allowListBasedAuth.AuthorizeRequest(ctx, req)`.
4. Current expected (buggy) behavior: `AuthorizeRequest` returns success (`AuthResult`) because only `ExpiryTimestamp` is checked.
5. Desired assertion for a fix: `AuthorizeRequest` should return an error (e.g., "request not allowlisted" or "request revoked") once revocation state is properly synced/considered — this assertion will fail against current code, demonstrating the bug.

### Citations

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L780-788)
```go
			activeAllowlistedRequests := []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}
			expiredRequestsCount := 0
			for _, request := range w.allowListedRequests {
				if int64(request.ExpiryTimestamp) > time.Now().Unix() {
					activeAllowlistedRequests = append(activeAllowlistedRequests, request)
				} else {
					expiredRequestsCount++
				}
			}
```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L1237-1260)
```go
	if w.lastSeenAllowlistedRequestsCount.Cmp(totalAllowlistedRequestsResult) == 0 {
		return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, totalAllowlistedRequestsResult, headAtLastRead, nil
	}

	var newAllowlistedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest
	readIdentifier = contractBinding.ReadIdentifier(GetActiveAllowlistedRequestsReverseMethodName)
	var endIndex = new(big.Int).Sub(totalAllowlistedRequestsResult, big.NewInt(1))
	var startIndex *big.Int

	for {
		var err error
		var response struct {
			AllowlistedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest
			SearchComplete      bool
			err                 error
		}

		// Start index should be no more than MaxResultsPerQuery away from end index
		startIndex = new(big.Int).Sub(endIndex, big.NewInt(MaxResultsPerQuery-1))
		// If start index is less than last seen allowlisted requests count, set it to last seen allowlisted requests
		// count to avoid duplicate requests
		if startIndex.Cmp(w.lastSeenAllowlistedRequestsCount) < 0 {
			startIndex = w.lastSeenAllowlistedRequestsCount
		}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L64-68)
```go
	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L113-120)
```go
func (r *allowListBasedAuth) fetchAllowlistedItem(allowListedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, digest [32]byte) *workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest {
	for _, item := range allowListedRequests {
		if item.RequestDigest == digest {
			return &item
		}
	}
	return nil
}
```
