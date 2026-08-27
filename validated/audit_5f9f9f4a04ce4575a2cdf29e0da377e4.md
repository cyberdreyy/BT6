### Title
Revoked-but-not-expired allowlisted requests remain authorized due to expiry-only pruning - ([File: core/services/workflows/syncer/v2/workflow_registry.go], [File: core/capabilities/vault/allow_list_based_auth.go])

### Summary
The in-memory cache of allowlisted vault requests (`w.allowListedRequests`) is only pruned based on `ExpiryTimestamp`, never re-validated against current on-chain revocation state, and `AuthorizeRequest` only checks expiry before granting authorization. This allows a request whose allowlist entry was explicitly revoked by the true owner (but not yet expired) to continue being accepted by the vault DON node.

### Finding Description
`syncAllowlistedRequests` periodically fetches new allowlisted entries via `w.getAllowlistedRequests` and merges them into `w.allowListedRequests`, pruning only entries whose `ExpiryTimestamp` has passed: [1](#0-0) 

There is no logic that removes an entry when it is revoked on-chain before expiry — the only pruning condition checked is `int64(request.ExpiryTimestamp) > time.Now().Unix()`. Once an entry has been fetched into `w.allowListedRequests`, it persists in memory until its `ExpiryTimestamp` naturally lapses, regardless of subsequent on-chain owner revocation.

`AuthorizeRequest` in `allowListBasedAuth` retrieves the cached list via `GetAllowlistedRequests` and only checks two things: whether the digest exists in the list, and whether `time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp)`: [2](#0-1) 

There is no re-check against current on-chain allowlist state or any revocation flag — expiry is the sole authorization gate.

Exploit flow: (1) attacker's `RequestDigest` is legitimately allowlisted by the true owner with some `ExpiryTimestamp` in the future; (2) the node's `syncAllowlistedRequests` loop fetches and caches this entry; (3) the owner revokes the allowlist entry on-chain before expiry; (4) the node's periodic sync does not remove the cached entry because the pruning loop only checks `ExpiryTimestamp`, not current on-chain existence; (5) attacker submits a gateway/vault request using the still-cached, revoked digest before `ExpiryTimestamp`; (6) `AuthorizeRequest` finds the digest in the cache, checks only expiry (which hasn't passed), and authorizes the request.

### Impact Explanation
This is an allowlist/authorization bypass: an attacker whose owner-granted vault-request permission has been explicitly revoked can continue to have vault requests authorized and executed at the true owner's expense until the original `ExpiryTimestamp` lapses. This matches the "allowlist/quota bypass" and "unauthorized action on another user's job/subscription" impact class described in the audit scope.

### Likelihood Explanation
Preconditions are realistic and require no special privilege beyond having previously been legitimately allowlisted: (1) attacker's digest was allowlisted by the owner, (2) owner later revokes it via a normal on-chain transaction before expiry, (3) attacker (an otherwise unprivileged gateway client) submits the same previously-allowlisted request before the original expiry time. No admin/operator access is needed — the attacker simply needs to have possessed valid credentials/allowlisting at some point and act before expiry. This is fully repeatable for any node that hasn't yet naturally pruned the entry via expiry, and the window can last up to the full remaining `ExpiryTimestamp` duration of the original grant.

### Recommendation
Change the pruning/authorization logic to treat "presence in the newly-fetched on-chain allowlist" as the source of truth rather than only pruning by expiry: on each sync cycle, replace `w.allowListedRequests` with only entries the contract currently reports as allowlisted (i.e., rebuild the set fully from `newAllowListedRequests`/on-chain reads rather than carrying forward old cached entries that merely haven't expired), or explicitly diff against a "still active on-chain" check before considering a cached entry valid. `AuthorizeRequest` should not treat "present in stale cache and not expired" as sufficient; it should reflect current on-chain revocation status.

### Proof of Concept
Go unit test plan (in `core/services/workflows/syncer/v2/workflow_registry_test.go` or `allow_list_based_auth_test.go`):
1. Seed `w.allowListedRequests` with an entry `{RequestDigest: D, Owner: O, ExpiryTimestamp: now+1hr}`.
2. Simulate revocation: on next `syncAllowlistedRequests` tick, mock `getAllowlistedRequests` to return `newAllowListedRequests = []` and `totalAllowlistedRequests` reflecting the on-chain count after removal — assert that after this sync, `D` is still present in `w.allowListedRequests` (demonstrating no revocation-based pruning), since the current pruning loop only checks `ExpiryTimestamp`.
3. Call `allowListBasedAuth.AuthorizeRequest` with a request whose digest is `D`, and assert it currently returns success (no error) — proving the bypass.
4. After applying the fix, re-run the same test and assert `AuthorizeRequest` returns `"request not allowlisted"` for `D` once the contract no longer lists it, even though `ExpiryTimestamp` has not passed.

### Citations

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L778-793)
```go
			w.allowListedMu.Lock()
			// Prune expired requests
			activeAllowlistedRequests := []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}
			expiredRequestsCount := 0
			for _, request := range w.allowListedRequests {
				if int64(request.ExpiryTimestamp) > time.Now().Unix() {
					activeAllowlistedRequests = append(activeAllowlistedRequests, request)
				} else {
					expiredRequestsCount++
				}
			}

			// Add new requests
			activeAllowlistedRequests = append(activeAllowlistedRequests, newAllowListedRequests...)
			w.allowListedRequests = activeAllowlistedRequests
			w.lastSeenAllowlistedRequestsCount = totalAllowlistedRequests
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L34-68)
```go
func (r *allowListBasedAuth) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	r.lggr.Debugw("AllowListBasedAuth authorizing request", "method", req.Method, "requestID", req.ID)
	requestDigest, err := req.Digest()
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to create digest", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, err
	}
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to decode digest", "method", req.Method, "requestID", req.ID, "requestDigest", requestDigest, "error", err)
		return nil, err
	}
	requestDigestBytes32 := [32]byte(requestDigestBytes)
	if r.workflowRegistrySyncer == nil {
		r.lggr.Errorw("AllowListBasedAuth workflowRegistrySyncer is nil", "method", req.Method, "requestID", req.ID)
		return nil, errors.New("internal error: workflowRegistrySyncer is nil")
	}
	allowlistedRequest, allowedRequestsStrs, err := r.findAllowlistedItemWithRetry(ctx, req, requestDigest, requestDigestBytes32)
	if err != nil {
		return nil, err
	}
	if allowlistedRequest == nil {
		r.lggr.Debugw("AllowListBasedAuth request digest not allowlisted",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"allowedRequestsStrs", allowedRequestsStrs)
		return nil, errors.New("request not allowlisted")
	}

	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}
```
