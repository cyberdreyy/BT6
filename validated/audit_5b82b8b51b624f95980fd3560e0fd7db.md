### Title
Missing zero-price validation in `createWriteOffRequest` lets any fulfiller steal escrowed tranche tokens for free - (contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
CVE-2021-45847 is a missing-input-validation bug (a parser accepts input that should have been rejected, causing a crash). The closest in-scope analog is `IdleCreditVaultWriteOffEscrow.createWriteOffRequest`, which validates `amount != 0` but never validates `underlyingsRequested != 0`. A request with `underlyingsRequested = 0` can then be fulfilled by anyone for `_underlyings = 0`, transferring the lender's escrowed tranche tokens to the fulfiller at zero cost.

### Finding Description
`createWriteOffRequest` pulls `amount` tranche tokens from the caller and stores a `WriteOffRequest{tranches, underlyings}`. Only `amount` is validated (contracts/IdleCreditVaultWriteOffEscrow.sol:86-101):

```solidity
if (amount == 0) revert NotAllowed();
// underlyingsRequested is never checked
userRequests[msg.sender] = WriteOffRequest({
  tranches: currentRequest.tranches + amount,
  underlyings: currentRequest.underlyings + underlyingsRequested
});
```

`fullfillWriteOffRequest` is explicitly callable by any wallet ("@dev this function can be called by any wallet", line 122) and only requires `_underlyings >= currentRequest.underlyings` (line 129). When `underlyings == 0`, a fulfiller passes `_underlyings = 0`, pays nothing, and receives all escrowed tranche tokens at line 151:

```solidity
IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
```

Note the mixed check also creates a subtle path: `createWriteOffRequest` is callable multiple times and accumulates `tranches` and `underlyings` additively, so a victim who ever created a `underlyingsRequested = 0` request (or whose existing request is topped up while `underlyings` stays low) can have a large accumulated tranche balance bought out for a nominal amount far below NAV — there is no check that the requested price bears any relation to tranche value.

### Impact Explanation
Direct theft: tranche tokens escrowed in the contract are transferred to an unprivileged fulfiller with zero (or dust) underlying paid. Tranche tokens represent a claim on vault NAV (`_trancheToUnderlyings` = `amount * tranchePrice / 1e18`), so the loss equals the full underlying value of the escrowed tranches. The lender irreversibly loses the position; the fulfiller can hold the tranches or exit them through `requestWithdraw`.

### Likelihood Explanation
Likelihood is limited to a configuration mistake: the lender must create a request with `underlyingsRequested == 0` (or a negligible value). There is no front-end-enforced floor since `fullfillWriteOffRequest` is permissionless and the contract never sanity-checks the ask. This matches the medium-severity profile of the CVE: a missing validation converts a user input error into a protocol-level theft, executable by any EOA in one transaction while an epoch is running.

### Recommendation
- Revert in `createWriteOffRequest` when `underlyingsRequested == 0`.
- Optionally enforce a minimum price floor (e.g., require `underlyings >= tranches * minPriceBps / FULL_VALUE`) so a request cannot be filled far below NAV.
- Consider requiring `userRequests[msg.sender].tranches == 0` on creation to prevent silent accumulation into a stale low-priced request.

### Proof of Concept
```solidity
// Foundry fork test; victim is a KYC'd AA tranche holder, attacker is any EOA.
function testWriteOffZeroPriceTheft() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);

    _startEpochAndCheckPrices(0); // write-off requests require running epoch

    // Victim mistakenly creates a request asking for 0 underlyings
    AAtranche.approve(address(escrow), type(uint256).max);
    escrow.createWriteOffRequest(amount, 0);

    address attacker = makeAddr("attacker");
    // Attacker fulfills with 0 underlyings — passes the >= check
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(address(this), amount, 0);

    assertEq(AAtranche.balanceOf(attacker), amount);   // attacker got all tranches
    assertEq(underlying.balanceOf(address(this)), 0);  // victim got nothing
}
```

Relevant code: `createWriteOffRequest` at `contracts/IdleCreditVaultWriteOffEscrow.sol:86-102` and `fullfillWriteOffRequest` at `contracts/IdleCreditVaultWriteOffEscrow.sol:123-155`.