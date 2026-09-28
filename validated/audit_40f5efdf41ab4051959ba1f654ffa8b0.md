# ANALOG ANALYSIS: ZenCash 51% / double-spend → double-draw on `defaultRecoveryReserve`

### Title
Post-default withdraw requests pay out 1:1 from the same `defaultRecoveryReserve` that was sized only for pre-default claims, letting post-default requesters drain recovery funds and freeze defaulted-epoch claimants' payouts - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The ZenCash incident is a double-spend: one pool of value was spent twice. The closest analog in this codebase is `IdleCreditVault`'s post-default withdrawal path. `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` to cover only the pre-default claim basis (`activeBasis + pendingBasis`) at `defaultRecoveryPrice`. After finalization, `requestWithdraw` mints receipt tokens for *new* post-default requests (`postDefaultRequests`), and `_claimPostDefaultWithdrawRequest` pays them **at par (1:1)** out of that same reserve via `_transferDefaultRecovery`. Two disjoint claimant sets — defaulted-epoch receipt holders (entitled to `basis × recoveryPrice`) and post-default requesters (paid `amount × 1`) — draw from one finite pot. The "one receipt one payout" invariant holds per receipt, but the solvency invariant for the reserve is broken: total obligations exceed the reserve by exactly `sum(postDefaultRequests)`, so the earliest claimants (attacker) over-consume and later defaulted claimants face permanent underflow reverts — a textbook double-spend of reserve backing.

### Finding Description
In `finalizeDefaultRecovery` the reserve is computed as:

```
reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve
recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis   // totalBasis excludes future postDefaultRequests
``` [1](#0-0) 

After this, an unprivileged KYC'd lender can still call `requestWithdraw` (via the CDO). Because `defaultRecoveryFinalized` is true, the code burns CDO-held strategy tokens, mints the user a receipt, and records `postDefaultRequests[_user] = _amount` — with **no underlying transfer and no increase in `defaultRecoveryReserve`**: [2](#0-1) 

The claim path then pays the post-default receipt 1:1 *from the recovery reserve*:

```
postDefaultRequests[_user] = 0;
_burn(_user, amount);
_transferDefaultRecovery(_user, amount);   // defaultRecoveryReserve -= amount
``` [3](#0-2) [4](#0-3) 

Meanwhile `_claimDefaultedWithdrawRequest` and `_claimDefaultedInstantWithdrawRequest` pay pre-default claimants `claimBasis × defaultRecoveryPrice` out of the *same* reserve: [5](#0-4) 

The reserve was sized as `totalBasis × recoveryPrice` where `totalBasis` only counted pre-default claims. Every post-default claim therefore consumes reserve that was earmarked for defaulted claimants. Since `_transferDefaultRecovery` reverts on underflow (`defaultRecoveryReserve -= _amount`), once cumulative post-default claims exceed `reserveAmount × (1 − recoveryPrice)` worth of slack, the remaining defaulted-epoch claimants' `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls revert permanently.

### Impact Explanation
Direct theft + permanent freezing of unclaimed yield: an attacker who is an ordinary KYC-passing lender (unprivileged) deposits/requests withdrawal after default finalization and claims before honest defaulted-epoch users, converting the reserve's haircut slack into a 1:1 payout for himself. Quantified loss = `min(sum(postDefaultRequests), reserveAmount)`; equivalently, defaulted claimants holding `claimBasis` lose up to `claimBasis × recoveryPrice` of entitled recovery, and their claims can never execute (underflow revert is permanent — the reserve cannot be replenished). With e.g. `recoveryPrice = 0.5e18` and an attacker post-default request equal to 20% of `totalBasis`, they capture 20% of the reserve while being entitled to none of it, leaving up to 20% of honest claims permanently unpayable.

### Likelihood Explanation
Requires a prior borrower default and recovery finalization — a rare but explicitly supported state (`defaulted`, `finalizeDefaultRecovery`, `postDefaultRequests` flow is production code, not test-only). Once in that state the attack needs no privileges, no timing race, and no collusion: any wallet passing `isWalletAllowed` can call the CDO's `requestWithdraw` and then `claimWithdrawRequest` in sequence. The guard at `requestWithdraw` line 247-251 only checks that the *requesting* user has no stale claims; nothing reserves or escrows backing for the new claim, and nothing caps `postDefaultRequests` against reserve slack. `_transferFundedClaim`'s reserve-isolation guard (line 899-905) protects the reserve from *funded* claims, but post-default claims deliberately route through `_transferDefaultRecovery`, so it does not apply.

### Recommendation
Either fund post-default requests separately or haircut them consistently:
- In `requestWithdraw`'s post-default branch, record the claim in a dedicated bucket paid via `_transferFundedClaim` and require the CDO to transfer actual underlying (e.g. reuse `collectWithdrawFunds`) rather than drawing on `defaultRecoveryReserve`; or
- Apply `defaultRecoveryPrice` to post-default payouts (`amount = postDefaultRequests[_user] * defaultRecoveryPrice / RECOVERY_FULL`) and include `postDefaultRequests` growth in the reserve accounting; or
- Track a per-claimant reserve entitlement (`recoveryReserveByUser`) so early claims can never spend another claimant's share.

### Proof of Concept
Foundry fork PoC (schematic, against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPostDefaultReserveDrain() public {
    // 1. Deposit honest users, run epoch 0
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _depositWithUser(honest, amount, true);       // honest lender
    _startEpochAndCheckPrices(0);

    // 2. Honest user requests withdraw (pending at default)
    vm.prank(honest);
    uint256 honestReq = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 3. Borrower defaults; manager/owner finalize with partial recovery
    //    -> defaultRecoveryPrice = 0.5e18, defaultRecoveryReserve = R
    _triggerDefaultAndFinalize(0.5e18);           // helper: borrower default + finalizeDefaultRecovery

    // 4. Attacker (KYC'd lender, unprivileged) deposits post-default at haircut
    //    price and immediately requests withdraw -> postDefaultRequests[attacker] = X
    _depositWithUser(attacker, amount, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // 5. Attacker claims 1:1 from the recovery reserve
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - pre, 0);

    // 6. Honest defaulted claim now reverts: reserve underflow in _transferDefaultRecovery
    vm.prank(honest);
    vm.expectRevert();                            // defaultRecoveryReserve -= amount underflows
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertion: `IdleCreditVault(address(strategy)).defaultRecoveryReserve()` decreases by the attacker's full request while `defaultRecoveryPrice < RECOVERY_FULL`, proving the reserve is spent twice — once for post-default claimants and once (in accounting) for defaulted-epoch claimants who can no longer be paid.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L252-257)
```text
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-692)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L760-767)
```text
  function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
    amount = postDefaultRequests[_user];
    if (amount == 0) return amount;
    postDefaultRequests[_user] = 0;
    // Post-default receipts are paid 1:1 because the haircut was applied when the request was made.
    _burn(_user, amount);
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
