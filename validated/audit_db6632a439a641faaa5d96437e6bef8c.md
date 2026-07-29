### Title
Malicious/blacklisted fee recipient can permanently block `EscrowDst.withdraw`/`publicWithdraw`, forcing forced cancellation and fee loss - (File: `contracts/EscrowDst.sol`)

### Summary
`EscrowDst._withdraw()` performs three sequential, unguarded token transfers — to the integrator fee recipient, the protocol fee recipient, and finally the maker — inside a single atomic call with no isolation between them. This is the same root-cause pattern as the referenced `withdrawTaxes()` bug: any single recipient in a loop/sequence of transfers can be made to revert (e.g., a malicious contract, or a token that reverts for a specific `to` address), which reverts the entire function and blocks payout to *all* other legitimate recipients.

### Finding Description
`_withdraw()` in `EscrowDst.sol` does: [1](#0-0) 

```solidity
if (integratorFeeAmount > 0) {
    _uniTransfer(immutables.token.get(), immutables.integratorFeeRecipientCd().get(), integratorFeeAmount);
}
if (protocolFeeAmount > 0) {
    _uniTransfer(immutables.token.get(), immutables.protocolFeeRecipientCd().get(), protocolFeeAmount);
}
uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
_uniTransfer(immutables.token.get(), immutables.maker.get(), amount);
```

`_uniTransfer` uses `IERC20.safeTransfer`, which reverts the whole transaction if the transfer fails: [2](#0-1) 

The `integratorFeeRecipient` and `protocolFeeRecipient` addresses are taken directly from `immutables.parameters`, which are populated at order-creation time by decoding `extraData` inside `BaseEscrowFactory._postInteraction()`: [3](#0-2)  This `extraData` is part of the order extension that the **maker** commits to and signs off-chain (the extension hash is embedded into the order's `salt`, per the Limit Order Protocol's extension-validation design), so a malicious maker fully controls which addresses are used as fee recipients for their own order — this is an unprivileged, self-service action, not a privileged/owner operation.

A malicious maker can set `integratorFeeRecipient` (or `protocolFeeRecipient`) to a contract that reverts unconditionally (or deploy/select a destination token that reverts transfers to that specific address, e.g. a blacklist-style token). Because there is no `try/catch` or per-recipient isolation, every call to `withdraw()` and `publicWithdraw()` on the resulting `EscrowDst` clone will revert for the entire withdrawal and public-withdrawal windows.

### Impact Explanation
Once withdrawal is permanently blocked:
- The honest taker/resolver, who funded the destination escrow with real tokens and a safety deposit, cannot claim any of their normal withdrawal path and must wait for the `DstCancellation` timelock to elapse and call `cancel()` instead: [4](#0-3)  This freezes the taker's escrowed destination-chain capital and safety deposit for the duration of the withdrawal window (temporary freezing of funds during the live swap lifecycle).
- `cancel()` returns the **full** `immutables.amount` to the taker with no fee deduction and no separate transfer to the integrator/protocol fee recipients — the protocol/integrator permanently lose the fee that would have been paid for this fill (permanent loss of fee-like value), since the escrow clone is a one-time contract and this fee cannot be recovered afterward.

This matches the bounty's High-severity bucket: "theft or permanent loss of unclaimed fee-like value, or temporary freezing of funds during the live swap lifecycle."

### Likelihood Explanation
High likelihood — no privileged role is required. Any user acting as an order maker can construct the extension/`extraData` for their own order (which they sign) to point `integratorFeeRecipient`/`protocolFeeRecipient` at a reverting contract, or otherwise craft the destination-side conditions to make one of the three sequential transfers fail unconditionally. Resolvers filling orders that reference attacker-controlled fee-recipient addresses have no on-chain way to detect this ahead of time, and would only discover the issue when `withdraw`/`publicWithdraw` starts reverting.

### Recommendation
Follow the same mitigations proposed for the analogous bug:
1. Wrap each of the three `_uniTransfer` calls in `EscrowDst._withdraw()` in a `try/catch` (or use low-level `call` with a bounded gas stipend and check the boolean result) so that a failing fee-recipient transfer does not block the maker's principal payout or the other fee transfer.
2. On failure, either skip the failed transfer (leaving funds recoverable via `rescueFunds` after the rescue delay) or re-route/queue the failed amount for later separate withdrawal, rather than reverting the entire `_withdraw()` call.

### Proof of Concept
1. Maker constructs and signs a Fusion order whose extension `extraData` sets `integratorFeeRecipient` to `MaliciousRevert` (a contract with a `receive`/fallback or ERC20 hook that always reverts) and a non-zero `integratorFee`.
2. Order is filled on the source chain; `BaseEscrowFactory._postInteraction` stores `integratorFeeRecipient` in `DstImmutablesComplement.parameters`.
3. Resolver deploys `EscrowDst` via `createDstEscrow`, funding it with the full destination amount plus safety deposit.
4. After the `DstWithdrawal` timelock opens, resolver calls `withdraw(secret, immutables)` (or any relayer calls `publicWithdraw`); `_uniTransfer(token, MaliciousRevert, integratorFeeAmount)` reverts, causing `_withdraw()` — and thus `withdraw`/`publicWithdraw` — to always revert.
5. Resolver is forced to wait until `DstCancellation` and call `cancel()`, recovering only the reclaimed `immutables.amount` (no fee taken) while the integrator/protocol fee is permanently lost and the resolver's capital was frozen for the whole withdrawal window.

### Citations

**File:** contracts/EscrowDst.sol (L64-73)
```text
    function cancel(Immutables calldata immutables)
        external
        onlyCaller(immutables.taker.get())
        onlyValidImmutables(immutables.hash())
        onlyAfter(immutables.timelocks.get(TimelocksLib.Stage.DstCancellation))
    {
        _uniTransfer(immutables.token.get(), immutables.taker.get(), immutables.amount);
        _ethTransfer(msg.sender, immutables.safetyDeposit);
        emit EscrowCancelled();
    }
```

**File:** contracts/EscrowDst.sol (L84-93)
```text
        uint256 integratorFeeAmount = immutables.integratorFeeAmountCd();
        uint256 protocolFeeAmount = immutables.protocolFeeAmountCd();
        if (integratorFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.integratorFeeRecipientCd().get(), integratorFeeAmount);
        }
        if (protocolFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.protocolFeeRecipientCd().get(), protocolFeeAmount);
        }
        uint256 amount = immutables.amount - integratorFeeAmount - protocolFeeAmount;
        _uniTransfer(immutables.token.get(), immutables.maker.get(), amount);
```

**File:** contracts/BaseEscrow.sol (L84-90)
```text
    function _uniTransfer(address token, address to, uint256 amount) internal {
        if (token == address(0)) {
            _ethTransfer(to, amount);
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
    }
```

**File:** contracts/BaseEscrowFactory.sol (L77-78)
```text
        address integratorFeeRecipient = address(bytes20(extraData[:20]));
        address protocolFeeRecipient = address(bytes20(extraData[20:40]));
```
