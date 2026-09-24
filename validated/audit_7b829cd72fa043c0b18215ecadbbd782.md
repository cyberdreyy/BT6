### Title
Blacklisted Redeem/Deposit Request Receiver Reverts Entire `solveRequestsVault` Batch, Freezing Unrelated Users' Requests - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.solveRequestsVault` processes an array of async deposit/redeem requests in a single transaction and, for non-expired requests, transfers the underlying ERC20 (e.g., USDC) directly to `request.receiver` using `token.safeTransfer(...)` with no try/catch or fallback. Because `request.receiver` is a user-controlled parameter set at `requestDeposit`/`requestRedeem` time, an ordinary user can pick (or later become) a blacklisted address for a blocklist-capable token such as USDC, causing the `safeTransfer` to revert and reverting the entire batch, including all other unrelated requests bundled with it by the solver.

### Finding Description
In `_solveRedeemVaultFixedPrice` and `_solveRedeemVaultAutoPrice`, when `request.deadline >= block.timestamp` (the "happy path"), tokens are sent straight to `request.receiver`: [1](#0-0) 
Same pattern for fixed price: [2](#0-1) 

Contrast this with the *expired/refund* path, and with `_clearHashAndTransferRefund`, which both use `_transferWithFallback`, a wrapper that falls back to the original requester if the transfer to `receiver` fails: [3](#0-2) 

This fallback wrapper is deliberately used to guard against exactly this failure mode elsewhere in the contract, but it is *not* used on the "happy path" transfers inside `_solveRedeemVaultFixedPrice`/`_solveRedeemVaultAutoPrice` (and the analogous deposit `enter()` calls, though `enter`/`exit` pull/push via the vault rather than directly to an arbitrary blacklistable address, so the redeem-token transfer is the concrete failure point).

These per-request solve functions are invoked in a loop inside `_solveRequestsVault`, which aggregates all requests from `solveRequestsVault` into one atomic transaction: [4](#0-3) 

`request.receiver` is fully attacker-controlled — any ordinary user calls `requestRedeem(token, unitsIn, minTokensOut, solverTip, deadline, maxPriceAge, isFixedPrice, receiver)` and specifies any `receiver` address, including one they know is (or will become) blacklisted by the token issuer (e.g., Circle for USDC): [5](#0-4) 

Because there is no per-request try/catch inside `_solveRequestsVault`'s loop, a single blacklisted-receiver redeem request causes `token.safeTransfer(request.receiver, ...)` to revert, which reverts the whole `solveRequestsVault` transaction — including every other legitimate user's deposit/redeem request batched by the (non-privileged-role-dependent) mechanic. Since solvers economically batch many requests per call to amortize gas, this lets one malicious/blacklisted request holder repeatedly block redemption processing for the whole queue, freezing other users' funds/units until the solver identifies and excludes the offending request off-chain.

### Impact Explanation
This freezes legitimate users' redeem/deposit requests (unclaimed tokens/units) as long as the malicious/blacklisted request remains in the batch, matching the "temporary freezing of user funds" Medium-severity impact class from the referenced report. Unlike the report's original ProtectionPool context, here the failure surface is `ProvisionerV2`'s async request queue with USDC as the bounty-listed asset, and the transfer that lacks failure-tolerance is a direct `safeTransfer` to a user-supplied `receiver`.

### Likelihood Explanation
Requires only an ordinary user calling `requestRedeem`/`requestDeposit` with a `receiver` address that is or later becomes blacklisted by the ERC20 issuer (USDC has 200+ historically blacklisted addresses) — no privileged role, oracle, or collusion needed. The solver's normal operation of batching multiple requests per `solveRequestsVault` call is what turns an individual blacklisting into a denial-of-service for all co-batched requests; this is standard solver behavior, not solver malice.

### Recommendation
Wrap the "happy path" token transfer to `request.receiver` in `_solveRedeemVaultFixedPrice`/`_solveRedeemVaultAutoPrice` (and any analogous direct-to-receiver transfer in `_solveRequestsVault`'s call graph) with the same `_transferWithFallback` pattern already used for refunds — falling back to `request.user` (or otherwise skipping/isolating the failing request) instead of allowing the failure to propagate and revert the entire batch.

### Proof of Concept
1. Deploy `ProvisionerV2` + `MultiDepositorVault` on a mainnet fork against real USDC.
2. Have User A deposit USDC and receive vault units.
3. User A calls `requestRedeem(USDC, unitsIn, minTokensOut, solverTip, deadline, maxPriceAge, isFixedPrice, receiver=0x5db0115f3b72d19cea34dd697cf412ff86dc7e1b)` (a known USDC-blacklisted address) to create an async redeem request.
4. Have User B (legitimate) also call `requestRedeem` with a valid, non-blacklisted receiver.
5. As the authorized solver, call `solveRequestsVault(USDC, [requestA, requestB], "", "")`.
6. Assert the transaction reverts (USDC `transfer` to the blacklisted address reverts inside `safeTransfer`), demonstrating User B's otherwise-valid redeem is blocked/frozen by User A's request in the same batch.
7. Repeat with `solveRequestsVault` only solving `[requestB]` to confirm it succeeds absent the poisoned request, proving the atomicity of the batch is the root cause of the freeze.

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L880-927)
```text
        bool depositsExist;
        for (uint256 i = 0; i < length; i++) {
            request = requests[i];
            if (_isRequestTypeDeposit(request.requestType)) {
                // Requirements: async deposit is enabled
                if (!tokenDetails.asyncDepositEnabled) {
                    // Log async deposit disabled event
                    emit AsyncDepositDisabled(i);
                    continue;
                }

                if (!depositsExist) {
                    depositsExist = true;
                    token.forceApprove(MULTI_DEPOSITOR_VAULT, type(uint256).max);
                }

                if (_isRequestTypeAutoPrice(request.requestType)) {
                    // Requirements + Effects + Interactions: solve auto price deposit
                    solverTip += _solveDepositVaultAutoPrice(
                        token, tokenDetails.asyncDepositMultiplier, request, priceAge, i
                    );
                } else {
                    // Requirements + Effects + Interactions: solve fixed price deposit
                    solverTip += _solveDepositVaultFixedPrice(
                        token, tokenDetails.asyncDepositMultiplier, request, priceAge, i
                    );
                }
            } else {
                // Requirements: async redeem is enabled
                if (!tokenDetails.asyncRedeemEnabled) {
                    // Log async redeem disabled event
                    emit AsyncRedeemDisabled(i);
                    continue;
                }

                if (_isRequestTypeAutoPrice(request.requestType)) {
                    // Requirements + Effects + Interactions: solve auto price redeem
                    solverTip += _solveRedeemVaultAutoPrice(
                        token, tokenDetails.asyncRedeemMultiplier, request, priceAge, i
                    );
                } else {
                    // Requirements + Effects + Interactions: solve fixed price redeem
                    solverTip += _solveRedeemVaultFixedPrice(
                        token, tokenDetails.asyncRedeemMultiplier, request, priceAge, i
                    );
                }
            }
        }
```

**File:** v3/src/core/ProvisionerV2.sol (L1281-1286)
```text
            asyncRequestHashes[redeemHash] = false;
            // Interactions: exit vault
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT)
                .exit(address(this), token, tokenOut, request.units, address(this));
            // Interactions: transfer tokens from provisioner to receiver
            token.safeTransfer(request.receiver, request.tokens);
```

**File:** v3/src/core/ProvisionerV2.sol (L1385-1395)
```text
    /// @notice Transfers amount to receiver and falls back to requester if receiver transfer fails
    /// @param token The ERC20 token being transferred
    /// @param requester The original requester address used as fallback receiver
    /// @param receiver The intended receiver from the request
    /// @param amount The amount of tokens to transfer
    function _transferWithFallback(IERC20 token, address requester, address receiver, uint256 amount) internal {
        // Interactions: transfer to receiver, fallback to requester on transfer failure
        if (receiver == requester || !token.trySafeTransfer(receiver, amount)) {
            token.safeTransfer(requester, amount);
        }
    }
```

**File:** v3/src/core/interfaces/IProvisionerV2.sol (L301-311)
```text

    /// @notice Deposit tokens directly into the vault
    /// @param token The token to deposit
    /// @param tokensIn The amount of tokens to deposit
    /// @param minUnitsOut The minimum amount of units expected
    /// @param receiver The address that receives units
    /// @return unitsOut The amount of shares minted to the receiver
    /// @dev Caller must be the receiver or approved by the receiver via {setDepositReceiverApproval}
    function deposit(IERC20 token, uint256 tokensIn, uint256 minUnitsOut, address receiver)
        external
        returns (uint256 unitsOut);
```
