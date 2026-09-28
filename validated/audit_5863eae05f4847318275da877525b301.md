### Title
Missing `underlyingsRequested > 0` validation lets anyone fulfill a write-off request for free and steal escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
Analogous to the `signatures_required == 0` bug (a quorum/threshold parameter accepted as zero, letting anyone satisfy the auth requirement), `IdleCreditVaultWriteOffEscrow.createWriteOffRequest()` validates that the deposited `amount` of tranche tokens is nonzero but never validates `underlyingsRequested`. A request with `underlyingsRequested == 0` can then be fulfilled by any wallet via `fullfillWriteOffRequest()` paying **zero** underlying tokens (and zero exit fee, since the fee is computed on the underlying amount), while receiving all of the escrowed tranche tokens. The same applies to any negligibly small price: the check `_underlyings < currentRequest.underlyings` only enforces a lower bound the lender happened to set, and the contract itself imposes no floor.

### Finding Description
- `createWriteOffRequest(uint256 amount, uint256 underlyingsRequested)` reverts only when `!isEpochRunning()` or `amount == 0`; `underlyingsRequested` is accepted as `0` and recorded in `userRequests[msg.sender]` and `pendingUnderlyings` (lines 86-102).
- `fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings)` reverts unless `currentRequest.tranches == _tranches` and `_underlyings >= currentRequest.underlyings` (lines 123-131). With `underlyings == 0`, passing `_underlyings = 0` succeeds.
- The fulfiller then pays `safeTransferFrom(msg.sender, this, 0)`, pays `_totFee = 0` (or nothing when `exitFee` applies to a 0 base), sends `_user` `_underlyings - _totFee = 0`, and receives `currentRequest.tranches` tranche tokens (lines 139-151).

Tranche tokens escrowed during a running epoch carry real claim value on vault assets (redeemable through `requestWithdraw`/claim flows or sellable back), so capturing them for 0 underlying is a direct transfer of value from the lender to the fulfiller.

### Impact Explanation
Direct theft of escrowed tranche tokens. Any EOA (the fulfiller role is explicitly "any wallet" per the NatSpec at line 122) that observes a pending write-off request priced at 0 (or dust) can atomically take the full `currentRequest.tranches` balance for nothing. The stolen amount equals the escrowed tranche tokens times the current `virtualPrice`, unbounded except by the request size. The invariant broken is "one receipt one payout": the escrow pays out the receipt (tranche tokens) without collecting the promised consideration.

### Likelihood Explanation
The trigger requires a lender to create a request with `underlyingsRequested == 0` (or a trivially small price), which is a front-end/input mistake — but the contract is precisely the layer expected to reject an economically nonsensical zero-price offer, and it already guards the symmetric `amount == 0` case. There is no other guard (skim, epoch gating, KYC, only-CDO) that stops a zero-price fulfillment, and fulfillment is permissionless and instantaneous, so any such request is captured immediately. Likelihood is therefore moderate: it depends on lender misconfiguration, but exploitation is guaranteed and costless once it occurs.

### Recommendation
Add a zero-check in `createWriteOffRequest`, e.g. `if (underlyingsRequested == 0) revert NotAllowed();` next to the existing `amount == 0` check (line 90). Optionally also enforce a minimum price per tranche token, and consider validating that `underlyingsRequested` is consistent with `amount` so a request cannot be accidentally priced far below fair value.

### Proof of Concept
Foundry fork test (drop into `test/foundry/` alongside `IdleCreditVaultWriteOffEscrow.t.sol`, reusing the same setup helpers that deploy `cdoEpoch`, `escrow`, `idleCDO`, `AAtranche`/`BBtranche`, `underlying`, and start an epoch):

```solidity
function testFulfillZeroPriceRequestStealsTranches() external {
    uint256 amount = 1000 * ONE_SCALE;
    // lender deposits BB and epoch is running (reuse existing helpers)
    idleCDO.depositBB(amount);
    _startEpochAndCheckPrices(0);

    uint256 tranches = IERC20(BBtranche).balanceOf(address(this));
    IERC20(BBtranche).approve(address(escrow), tranches);

    // lender mistakenly creates a request asking for 0 underlyings
    escrow.createWriteOffRequest(tranches, 0);

    address attacker = makeAddr("attacker");
    // attacker needs no underlying at all
    vm.startPrank(attacker);
    underlying.approve(address(escrow), 0);
    escrow.fullfillWriteOffRequest(address(this), tranches, 0);
    vm.stopPrank();

    // attacker got all escrowed tranche tokens for free
    assertEq(IERC20(BBtranche).balanceOf(attacker), tranches);
    // lender received nothing
    assertEq(underlying.balanceOf(address(this)), 0);
    // attacker can still redeem them at virtualPrice via normal withdraw flow
}
```

Note: I was unable to open `IdleCDOEpochVariant.sol` to verify line numbers for `isEpochRunning`/`virtualPrice` helpers, so the PoC reuses the existing test harness helpers (`_startEpochAndCheckPrices`, `ONE_SCALE`, `escrow`) rather than exact signatures — the core logic (`createWriteOffRequest` accepting 0 and `fullfillWriteOffRequest` paying 0) is confirmed directly in the escrow source cited above.