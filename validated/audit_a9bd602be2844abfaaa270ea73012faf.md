### Title
Deposit cancellation with a `Revert on Zero Value Transfers` token permanently DoSes `cancelRequest` when the cancellation fee equals the deposit amount - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`ProvisionerV2.cancelRequest()` allows the deposit cancellation fee to equal the full escrowed deposit amount (`cancellationFee <= refundAmount`, not `<`), which zeroes `refundAmount`. This zero amount is then passed unconditionally to `_transferWithFallback` → `token.safeTransfer`, which lacks any `amount > 0` guard, unlike `MultiDepositorVault.enter/exit` which explicitly gate transfers with `if (tokenAmount > 0)`. For a deposit token that reverts on zero-value transfers, this reverts the entire `cancelRequest` call, DoS-ing the user's self-cancel path for that specific request.

### Finding Description
In `cancelRequest` (deposit branch), `v3/src/core/ProvisionerV2.sol:331-348`:
```solidity
uint256 refundAmount = request.tokens;
if (block.timestamp < request.deadline) {
    uint256 cancellationFee = _computeDepositCancellationFeeTokens(token);
    require(cancellationFee <= refundAmount, Aera__CancellationFeeExceedsRequestAmount());
    if (cancellationFee != 0) {
        refundAmount -= cancellationFee;
        token.safeTransfer(MULTI_DEPOSITOR_VAULT, cancellationFee);
    }
}
_clearHashAndTransferRefund(token, request, msg.sender, refundAmount, token);
```
The `<=` comparison permits `cancellationFee == refundAmount`, making the post-fee `refundAmount == 0`. This flows to `_clearHashAndTransferRefund` (`v3/src/core/ProvisionerV2.sol:1362-1383`) which calls `_transferWithFallback(transferToken, requester, request.receiver, amount)` with `amount == 0` and `transferToken == token` (the underlying deposit asset).

`_transferWithFallback` (`v3/src/core/ProvisionerV2.sol:1390-1395`):
```solidity
function _transferWithFallback(IERC20 token, address requester, address receiver, uint256 amount) internal {
    if (receiver == requester || !token.trySafeTransfer(receiver, amount)) {
        token.safeTransfer(requester, amount);
    }
}
```
Neither the `trySafeTransfer` attempt nor the fallback `token.safeTransfer` call is gated on `amount > 0`. For a "revert on zero value transfer" token — a token class the protocol's README/scope explicitly claims to support (per the analog Ammplify report) — a zero-amount `transfer` call reverts, and since `_transferWithFallback`'s fallback branch is an unconditional direct call (not wrapped in try/catch), the entire `cancelRequest` transaction reverts.

Contrast this with `MultiDepositorVault.enter`/`exit` (`v3/src/core/MultiDepositorVault.sol:61-90`), which correctly guard: `if (tokenAmount > 0) token.safeTransferFrom(...)` / `if (tokenAmount > 0) token.safeTransfer(...)`. The `ProvisionerV2` refund/cancel paths (`_transferWithFallback`, `_clearHashAndTransferRefund`, `cancelRequest`) lack this same guard.

An ordinary depositor fully controls `tokensIn` at `requestDeposit` time (`v3/src/core/ProvisionerV2.sol:764-809`), and `_computeDepositCancellationFeeTokens(token)` is deterministic from the configured numeraire fee and current price at cancellation time. A user can pick a deposit size such that the fee-token conversion equals their deposit amount, guaranteeing `refundAmount == 0` on self-cancel.

### Impact Explanation
This causes a temporary freeze of the user's own escrowed deposit funds for the affected request: the ordinary self-cancel path (`cancelRequest`) permanently reverts for that specific request (deterministic, not price-dependent since it's evaluated at cancel time against a config value), preventing early cancellation. The user must wait until `request.deadline` passes and rely on `refundRequest`, which uses the *original* `request.tokens` (never reduced to zero) and is therefore unaffected. This matches a "temporary freeze of user funds" — Medium severity under typical Immunefi vault classifications — since funds are inaccessible via the intended function until the deadline, and the cancellation-fee revenue path is also non-functional in this edge case.

### Likelihood Explanation
Requires (1) the underlying deposit token to be a "revert on zero value transfer" token — a token type the protocol claims to support per its documented scope, and (2) the depositor choosing a `tokensIn` amount that exactly matches the token-denominated cancellation fee at cancel time, which is fully within an ordinary user's control (self-triggered, not adversarial against other users) and deterministic (no oracle/MEV dependency beyond the deposit-cancellation fee's own price conversion). This is a niche but concretely reachable edge case limited to self-cancellation of a specific deposit request.

### Recommendation
Guard the refund transfer with an amount check, mirroring `MultiDepositorVault`'s pattern:
```solidity
function _transferWithFallback(IERC20 token, address requester, address receiver, uint256 amount) internal {
    if (amount == 0) return;
    if (receiver == requester || !token.trySafeTransfer(receiver, amount)) {
        token.safeTransfer(requester, amount);
    }
}
```
Additionally, tighten `cancelRequest`'s check to `require(cancellationFee < refundAmount, ...)` (or explicitly skip the refund transfer when `refundAmount == 0`) so a full-fee cancellation cannot leave a dangling zero-amount transfer.

### Proof of Concept
Foundry outline (fork test):
1. Deploy/mock an ERC20 that reverts when `transfer`/`transferFrom` is called with `amount == 0` (e.g., wrap USDT-style zero-transfer-revert behavior or use a purpose-built mock), and configure it as an accepted deposit token in `ProvisionerV2` with async deposits enabled.
2. Set `_depositCancellationFeeNumeraire` (via `setCancellationDetails`) to a nonzero value and compute the corresponding token amount via `_computeDepositCancellationFeeTokens`.
3. As an ordinary user, call `requestDeposit(token, tokensIn, minUnitsOut, ...)` with `tokensIn` chosen to exactly equal the fee-token amount computed in step 2.
4. Before `deadline`, call `cancelRequest(token, request)`.
5. Assert the call reverts (bubbling the zero-value-transfer token's revert), even though `cancellationFee <= refundAmount` passes the `require`, proving the self-cancel path is DoS'd until `deadline` passes, at which point `refundRequest` must be used instead — asserting funds are locked from the intended cancel path for that window.