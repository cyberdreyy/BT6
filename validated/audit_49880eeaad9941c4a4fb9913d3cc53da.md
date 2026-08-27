Found a concrete analog: the Vault gateway's allowlist authorization reads on-chain `WorkflowRegistry` allowlist entries using `primitives.Unconfirmed` confidence, meaning it accepts entries from blocks that have not reached finality — directly analogous to the Fortuna bug of revealing a secret/authorizing an action before the corresponding on-chain commitment is final.

### Title
Vault gateway authorizes secret mutations based on unconfirmed (non-finalized) WorkflowRegistry allowlist entries, enabling reorg-based authorization bypass - (File: core/services/workflows/syncer/v2/workflow_registry.go)

### Summary
The Vault DON's `AllowListBasedAuth` path authorizes `secrets.create` / `secrets.update` / `secrets.delete` requests by checking whether the request digest appears in the in-memory cache populated from the on-chain `WorkflowRegistry` contract's allowlist. That cache is refreshed by `workflowRegistry.getAllowlistedRequests`, which reads contract state at `primitives.Unconfirmed` confidence rather than requiring finality, so an allowlist entry from a block that can still be reorged out is treated as valid authorization for gateway-routed secret operations.

### Finding Description
`allowListBasedAuth.AuthorizeRequest` (`core/capabilities/vault/allow_list_based_auth.go`) computes the request digest and looks it up via `findAllowlistedItemWithRetry`, which calls `r.workflowRegistrySyncer.GetAllowlistedRequests(ctx)` [1](#0-0) . That in-memory list is populated by `workflowRegistry.syncAllowlistedRequests`, which periodically calls `w.getAllowlistedRequests(ctx, w.contractReader)` and merges the results into `w.allowListedRequests` [2](#0-1) .

Crucially, `getAllowlistedRequests` reads both the `totalAllowlistedRequests` count and the `getActiveAllowlistedRequestsReverse` results using `primitives.Unconfirmed` as the confidence level: [3](#0-2) [4](#0-3) 

Unlike the log poller / head tracker's finality-depth/finality-tag machinery used elsewhere in the node for transaction and reorg protection [5](#0-4) , this allowlist read path has no finality gating — it takes whatever the RPC currently reports as latest/unconfirmed state.

Once an entry is fetched into `w.allowListedRequests`, the digest is authorized: `AuthResult` is returned and the caller (`GatewayVaultRequestProcessor.authorizeAndStamp` / `GatewayHandler.HandleGatewayMessage`) immediately proceeds to invoke `SecretsService.CreateSecrets` / `UpdateSecrets` / `DeleteSecrets` on the node [6](#0-5) . There is no re-check against a finalized block before or after the mutation is applied, and the replay guard (`RequestReplayGuard`) only prevents the same digest from being reused — it does not verify finality either [7](#0-6) .

This mirrors the reported bug class exactly: state is treated as authoritative before it is actually irreversible, and an attacker who can force even a shallow reorg (or exploit an RPC endpoint reporting unconfirmed/unsynced state) can get a workflow-owner allowlist entry observed by nodes, have the Vault DON create/update/delete secrets under that owner's authorization, and then have the originating on-chain allowlist transaction reorged away — leaving the secret mutation (an unauthorized, effectively unpaid/uncommitted action) already executed.

### Impact Explanation
If the allowlisting transaction is reorged out after the gateway/node already consumed it to authorize a secret creation/update/deletion, the DON has performed a privileged mutation (creating, overwriting, or deleting a workflow owner's encrypted secrets) that was never actually finalized on-chain. Because secrets underpin workflow execution (e.g., API keys, credentials used by CRE workflows), this can lead to secret disclosure/loss, unauthorized secret injection under an owner identity that never truly authorized it, or destructive deletion — a direct violation of the "unauthorized job run or fund movement" / "cross-user response confusion" criteria for this analysis, since the mutation is bound to a workflow owner without a finalized on-chain commitment backing it.

### Likelihood Explanation
Exploitation requires the ability to influence or predict a short reorg (or interact with an RPC provider serving unconfirmed/soon-to-be-reverted state) around the moment the allowlist transaction is mined, combined with timing the vault request to land inside the ~5s `syncAllowlistedRequests` tick window before the reorg completes. This is harder to pull off than a purely local bug but is a realistic risk on L2s/PoS chains with non-trivial reorg windows or against nodes pointed at less-trustworthy/laggy RPC endpoints, matching the report's premise that "confirmation depth ≠ finality" for Ethereum PoS/L2s.

### Recommendation
Read `totalAllowlistedRequests` and `getActiveAllowlistedRequestsReverse` at `primitives.Finalized` (or the chain's equivalent finality confidence level) instead of `primitives.Unconfirmed` in `workflowRegistry.getAllowlistedRequests`, or add an explicit finality check before merging new entries into `w.allowListedRequests` in `syncAllowlistedRequests`. Where latency requirements make this costly, at minimum gate destructive/mutating vault operations (`create`, `update`, `delete`) behind a finalized-read confirmation while allowing read-only operations to use faster confidence levels.

### Proof of Concept
1. Attacker submits a `WorkflowRegistry.allowlistRequest(...)` transaction on a chain with a finality gadget (e.g., an L2 or PoS-based chain), allowlisting a `secrets.delete`/`secrets.create` digest for a target owner.
2. Within the next `syncAllowlistedRequests` tick (default 5s, `core/services/workflows/syncer/v2/workflow_registry.go:53`), the Vault DON nodes read the entry via `GetLatestValueWithHeadData(..., primitives.Unconfirmed, ...)` and cache it as active.
3. Attacker (or colluding actor) immediately sends the corresponding gateway vault request; `allowListedAuth.AuthorizeRequest` matches the digest and authorizes it, and `GatewayHandler.HandleGatewayMessage` executes the secret mutation.
4. Attacker forces/benefits from a reorg that drops the original allowlisting block before it finalizes, so on canonical/finalized chain state the allowlist entry never existed — yet the secret mutation has already been performed by the DON, based on state that was never final.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L79-92)
```go
func (r *allowListBasedAuth) findAllowlistedItemWithRetry(ctx context.Context, req jsonrpc.Request[json.RawMessage], requestDigest string, requestDigestBytes32 [32]byte) (*workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, []string, error) {
	for attempt := 0; attempt <= r.retryCount; attempt++ {
		allowedRequests := r.workflowRegistrySyncer.GetAllowlistedRequests(ctx)
		allowedRequestsStrs := make([]string, 0, len(allowedRequests))
		for _, rr := range allowedRequests {
			allowedReqStr := fmt.Sprintf("AuthorizedOwner: %s, RequestDigest: %s, ExpiryTimestamp: %d", rr.Owner.Hex(), hex.EncodeToString(rr.RequestDigest[:]), rr.ExpiryTimestamp)
			allowedRequestsStrs = append(allowedRequestsStrs, allowedReqStr)
		}
		r.lggr.Debugw("AllowListBasedAuth loaded allowlisted requests", "method", req.Method, "requestID", req.ID, "attempt", attempt+1, "allowedRequests", allowedRequestsStrs)

		allowlistedRequest := r.fetchAllowlistedItem(allowedRequests, requestDigestBytes32)
		if allowlistedRequest != nil {
			return allowlistedRequest, allowedRequestsStrs, nil
		}
```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L772-801)
```go
		case <-ticker:
			newAllowListedRequests, totalAllowlistedRequests, head, err := w.getAllowlistedRequests(ctx, w.contractReader)
			if err != nil {
				w.lggr.Errorw("failed to call getAllowlistedRequests", "err", err)
				continue
			}
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
			w.lggr.Debugw("synced allowlisted requests",
				"newRequestsNum", len(newAllowListedRequests),
				"expiredRequestsNum", expiredRequestsCount,
				"activeRequestsNum", len(w.allowListedRequests),
				"lastSeenOnchainRequestsNum", w.lastSeenAllowlistedRequestsCount,
				"blockHeight", head.Height,
			)
			w.allowListedMu.Unlock()
```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L1226-1240)
```go
	// Read current total allowlisted requests
	var headAtLastRead *types.Head
	var totalAllowlistedRequestsResult *big.Int
	readIdentifier := contractBinding.ReadIdentifier(TotalAllowlistedRequestsMethodName)
	headAtLastRead, err := contractReader.GetLatestValueWithHeadData(
		ctx, readIdentifier, primitives.Unconfirmed, nil, &totalAllowlistedRequestsResult,
	)
	if err != nil {
		return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, w.lastSeenAllowlistedRequestsCount, &types.Head{Height: "0"}, errors.New("failed to get latest value with head data. error: " + err.Error())
	}

	if w.lastSeenAllowlistedRequestsCount.Cmp(totalAllowlistedRequestsResult) == 0 {
		return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, totalAllowlistedRequestsResult, headAtLastRead, nil
	}

```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L1262-1275)
```go
		params := GetActiveAllowlistedRequestsReverseParams{
			EndIndex:   endIndex,
			StartIndex: startIndex,
		}
		w.lggr.Debugw("getting active allowlisted requests",
			"endIndex", endIndex,
			"startIndex", startIndex,
		)
		headAtLastRead, err = contractReader.GetLatestValueWithHeadData(
			ctx, readIdentifier, primitives.Unconfirmed, params, &response,
		)
		if err != nil {
			return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, w.lastSeenAllowlistedRequestsCount, &types.Head{Height: "0"}, errors.New("failed to get latest value with head data. error: " + err.Error())
		}
```

**File:** core/config/docs/chains-evm.toml (L36-59)
```text
# FinalityDepth is the number of blocks after which an ethereum transaction is considered "final". Note that the default is automatically set based on chain ID, so it should not be necessary to change this under normal operation.
# BlocksConsideredFinal determines how deeply we look back to ensure that transactions are confirmed onto the longest chain
# There is not a large performance penalty to setting this relatively high (on the order of hundreds)
# It is practically limited by the number of heads we store in the database and should be less than this with a comfortable margin.
# If a transaction is mined in a block more than this many blocks ago, and is reorged out, we will NOT retransmit this transaction and undefined behaviour can occur including gaps in the nonce sequence that require manual intervention to fix.
# Therefore, this number represents a number of blocks we consider large enough that no re-org this deep will ever feasibly happen.
#
# Special cases:
# `FinalityDepth`=0 would imply that transactions can be final even before they were mined into a block. This is not supported.
# `FinalityDepth`=1 implies that transactions are final after we see them in one block.
#
# Examples:
#
# Transaction sending:
# A transaction is sent at block height 42
#
# `FinalityDepth` is set to 5
# A re-org occurs at height 44 starting at block 41, transaction is marked for rebroadcast
# A re-org occurs at height 46 starting at block 41, transaction is marked for rebroadcast
# A re-org occurs at height 47 starting at block 41, transaction is NOT marked for rebroadcast
FinalityDepth = 50 # Default
# FinalityTagEnabled means that the chain supports the finalized block tag when querying for a block. If FinalityTagEnabled is set to true for a chain, then FinalityDepth field is ignored.
# Finality for a block is solely defined by the finality related tags provided by the chain's RPC API. This is a placeholder and hasn't been implemented yet.
FinalityTagEnabled = false # Default
```

**File:** core/capabilities/vault/gw_handler.go (L180-223)
```go
func (h *GatewayHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) (err error) {
	reqLggr := h.requestLogger(req, gatewayID)
	reqLggr.Debugw("received message from gateway", "req", req)

	var response *jsonrpc.Response[json.RawMessage]
	var authResult *AuthResult

	switch req.Method {
	case vaulttypes.MethodSecretsCreate, vaulttypes.MethodSecretsUpdate:
		publicKey, pkErr := h.getMasterPublicKey(ctx)
		if pkErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pkErr)
			break
		}
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, publicKey)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}

	if response == nil {
		switch req.Method {
		case vaulttypes.MethodSecretsCreate:
			response = h.handleSecretsCreate(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsUpdate:
			response = h.handleSecretsUpdate(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsDelete:
			response = h.handleSecretsDelete(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsList:
			response = h.handleSecretsList(ctx, gatewayID, req, authResult)
		}
```

**File:** core/capabilities/vault/request_replay_guard.go (L30-47)
```go
// CheckAndRecord returns ErrRequestAlreadySeen if the digest was previously
// recorded and has not yet expired. Otherwise it records the digest with
// the given expiry timestamp (unix seconds, UTC).
//
// Expired entries are cleaned up on every call.
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
