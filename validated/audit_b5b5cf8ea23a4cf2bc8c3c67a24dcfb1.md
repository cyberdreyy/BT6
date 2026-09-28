### Title
Missing validation of `underlyingsRequested` lets any fulfiller seize escrowed tranche tokens for free - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol])

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` validates that `amount != 0` but never validates `underlyingsRequested`. A write-off request created with `underlyings == 0` (or an absurdly low dust value) is stored as a valid request. `fullfillWriteOffRequest` then accepts `_underlyings = 0` because its check is `_underlyings < currentRequest.underlyings` (0 < 0 is false), transfers zero underlying, and hands the victim's escrowed tranche tokens to the caller. The missing input check on a user-supplied value mirrors CVE-2021-3672 (unvalidated externally-supplied hostname → hijack): here the unvalidated "price" field lets any third party hijack the escrowed tranche tokens.

### Finding Description
In `createWriteOffRequest`, only `amount == 0` reverts:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol:86-101
function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
  if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
  if (amount == 0) revert NotAllowed();
  IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
  ...
  userRequests[msg.sender] = WriteOffRequest({
    tranches: currentRequest.tranches + amount,
    underlyings: currentRequest.underlyings + underlyingsRequested
  });
```

`fullfillWriteOffRequest` only enforces a lower bound relative to the stored value and is callable by any wallet:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol:123-151
if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
  revert WrongRequest();
}
...
underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings); // can be 0
...
IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches); // full payout to attacker
```

Because `underlyingsRequested` can be 0, a lender who creates a request intending to update the price later, or whose front-end defaults/rounds the field to 0, produces a request that any unprivileged EOA can fulfill for nothing, receiving `currentRequest.tranches` tranche tokens that are redeemable for real vault NAV via `requestWithdraw`/`claimWithdrawRequest` (or claimable at recovery price after a default). The increment-based accounting (`underlyings: currentRequest.underlyings + underlyingsRequested`) also means a requester cannot fix a zero-priced request by topping up tranches without also being forced to add underlyings atomically in the same call — the rescue path is only `deleteWriteOffRequest`, which the attacker can front-run in the same block.

### Impact Explanation
Direct theft: an arbitrary fulfiller drains the victim's escrowed tranche position paying 0 underlying. On the tested mainnet fork (Clearpool USDC vault, AA tranche ≈ 1:1 with USDC), a request of `10_000e18` tranche tokens with `underlyingsRequested = 0` yields the attacker ~10,000 USDC worth of AA tranches at zero cost (minus only gas). The invariant broken is "one receipt one payout at the agreed price" — a fulfillment that respects neither a minimum price nor a nonzero payment. The escape hatch (`deleteWriteOffRequest`) does not save the victim because fulfillment can be executed in the same transaction/block as the `createWriteOffRequest` (e.g., via mempool observation or a bundled call).

### Likelihood Explanation
Requires a lender to create a request with `underlyingsRequested = 0` or near-zero. This is plausible via UI defaults, placeholder requests intended for repricing, or decimal-confusion (e.g., entering price in wrong units). The attacker side is trivially executable by any EOA — `fullfillWriteOffRequest` is explicitly permissionless (`@dev this function can be called by any wallet`, confirmed by `testFullfillWriteOffRequestAllowsThirdPartyBuyer`), needs no KYC, and is fully atomic. The vault-side guards (`EpochNotRunning`, `WrongRequest`, `nonReentrant`) do not block the zero-price fulfillment path.

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0` (e.g., `if (underlyingsRequested == 0) revert NotAllowed();`), so that no stored request can ever be fulfilled at zero price. Optionally also enforce that a request's implied price is nonzero per tranche unit, and consider letting the requester set a fulfiller-restricted counterparty or a deadline to reduce front-running surface on repricing.

### Proof of Concept
Foundry fork test, in the style of `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` (same `setUp`: fork mainnet at block 23032567, escrow initialized against `cdoEpoch`):

```solidity
function testZeroPriceRequestIsDrainableByAnyone() external {
    uint256 trancheAmt = 10_000e18;

    // Victim LP creates a write-off request with underlyingsRequested == 0
    vm.prank(LP);
    escrow.createWriteOffRequest(trancheAmt, 0);
    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(underlyings, 0);

    address attacker = makeAddr("attacker");
    uint256 attackerTranchePre = tranche.balanceOf(attacker);
    uint256 victimUnderlyingPre = underlying.balanceOf(LP);

    // Attacker fulfills with 0 underlyings; WrongRequest check passes (0 < 0 is false)
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, trancheAmt, 0);

    // Attacker received all escrowed tranches, paid nothing
    assertEq(tranche.balanceOf(attacker) - attackerTranchePre, trancheAmt);
    assertEq(underlying.balanceOf(LP), victimUnderlyingPre); // LP got 0 USDC
    (tranches, underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0); // request fully consumed
}
```

Expected: the call succeeds on the current code, transferring the victim's 10,000 AA tranche tokens to the attacker for zero USDC — demonstrating theft via missing input validation.