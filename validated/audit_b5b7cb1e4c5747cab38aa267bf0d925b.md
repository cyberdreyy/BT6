### Title
LogPoller checkpoint reposition endpoint reachable with run-role authorization, permitting a low-privilege user to force skipped/duplicated on-chain events - (File: core/web/lp_skip_controller.go)

### Summary
`LPSkipController.LPSkipToBlock` accepts an arbitrary attacker-supplied `blockNumber`, `family`, and `chain-id` and directly repositions the LogPoller checkpoint via `App.LPSkipToBlock` with only numeric/family/chain-id sanity checks and no bound relative to the current head or job dependencies. Because this route is gated by `RequiresRunRole` rather than a stricter edit/admin role, any authenticated run-role session (a low-privilege credential tier) can invoke this destructive, node-wide infrastructure operation.

### Finding Description
The handler validates only that `BlockNumber >= 2`, `Family == relay.NetworkEVM`, and `ChainID` is non-empty [1](#0-0) , then calls `c.App.LPSkipToBlock(ctx, request.Family, request.ChainID, request.BlockNumber)` unconditionally on success of those checks [2](#0-1) . There is no check that the requested block is within a sane range relative to the current chain head, no check that it doesn't rewind past already-finalized/processed blocks, and no scoping to a specific job or contract — the operation affects the shared LogPoller state for the entire chain, impacting every job/listener relying on that chain's log stream. The route is exposed as `POST /v2/lp_skip_to_block` and, per the stated gating in this codebase, is protected only by `RequiresRunRole`, a lower-privilege tier than the edit/admin roles typically required for destructive infrastructure mutations elsewhere in the admin API.

### Impact Explanation
A run-role-only credential — lower privilege than edit/admin — can force the node's LogPoller to jump to an attacker-chosen block for a target chain. Setting the checkpoint forward skips already-finalized logs that jobs depend on (missed job triggers, e.g., missed Automation/Keeper or Direct Request log-triggered runs), while setting it backward can cause re-processing/duplication of already-handled logs (duplicate job runs, potentially duplicate fund-moving actions if a job reacts to on-chain events). This matches the "unauthorized job run" / node availability-and-integrity impact class, exercised by a credential tier that should not be able to perform node-wide destructive infra operations.

### Likelihood Explanation
The only precondition is possession of a run-role session/API token — a credential tier explicitly included in the unprivileged-attacker model for this audit. The request is a single unauthenticated-relative-to-admin POST with a trivially guessable body (`blockNumber`, `family`, `chain-id`), requiring no special timing or race condition, and is fully repeatable against any configured EVM chain.

### Recommendation
Require a stricter role (edit/admin) for `POST /v2/lp_skip_to_block`, consistent with other destructive infra-mutation endpoints, and add bounds validation in `LPSkipToBlock` (e.g., disallow rewinding/advancing beyond the node's finality window or currently persisted LogPoller checkpoint without an explicit force flag reserved for admin), plus audit logging of who invoked the operation and for which chain.

### Proof of Concept
Handler-level integration test plan:
1. Build a test `web.Server`/router configured with a session/API token assigned only the run role (not edit/admin).
2. Seed the LogPoller for a configured EVM `chain-id` with a known current head/checkpoint.
3. Issue `POST /v2/lp_skip_to_block` with `{"blockNumber": <arbitrary value less than current checkpoint or far beyond head>, "family": "evm", "chain-id": "<test-chain-id>"}` using the run-role session.
4. Assert response is `200 OK` with body confirming `blockNumber` was accepted (per `LPSkipToBlockResponse`).
5. Assert the underlying LogPoller's stored checkpoint was mutated to the attacker-supplied value (via `App.LPSkipToBlock`'s downstream state), demonstrating the run-role user successfully repositioned checkpoint state without edit/admin privilege.

### Citations

**File:** core/web/lp_skip_controller.go (L34-51)
```go
	if request.BlockNumber < 2 {
		jsonAPIError(gctx, http.StatusUnprocessableEntity, errors.Errorf("block number must be >= 2: %v", request.BlockNumber))
		return
	}

	if request.Family == "" {
		jsonAPIError(gctx, http.StatusUnprocessableEntity, errors.New("chain family was not provided"))
		return
	}
	if request.Family != relay.NetworkEVM {
		jsonAPIError(gctx, http.StatusUnprocessableEntity, errors.Errorf("unsupported chain family %q, only %s is supported", request.Family, relay.NetworkEVM))
		return
	}

	if strings.TrimSpace(request.ChainID) == "" {
		jsonAPIError(gctx, http.StatusUnprocessableEntity, errors.New("chain-id was not provided"))
		return
	}
```

**File:** core/web/lp_skip_controller.go (L53-61)
```go
	ctx := gctx.Request.Context()
	if err := c.App.LPSkipToBlock(ctx, request.Family, request.ChainID, request.BlockNumber); err != nil {
		if errors.Is(err, chainlink.ErrNoSuchRelayer) {
			jsonAPIError(gctx, http.StatusBadRequest, err)
			return
		}
		jsonAPIError(gctx, http.StatusInternalServerError, err)
		return
	}
```
