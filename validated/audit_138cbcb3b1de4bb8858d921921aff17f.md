### Title
Post-default `requestWithdraw` mints unfunded 1:1 receipts paid out of the fixed default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery` sets `defaultRecoveryFinalized`, any tranche holder can call `requestWithdraw` on the CDO. In `IdleCreditVault.requestWithdraw` (lines 247-257), the strategy burns the CDO's strategy tokens, mints the user an equal receipt, and records it in `postDefaultRequests[_user]` — but no underlying is transferred into the strategy and `defaultRecoveryReserve` is not increased. `_claimPostDefaultWithdrawRequest` (lines 760-767) then pays that receipt 1:1 via `_transferDefaultRecovery`, drawing down the same fixed reserve that backs defaulted-epoch receipts at `defaultRecoveryPrice < 1`. The invariant "one receipt, one funded payout" is broken: post-default receipts are created against reserve they were never funded for.

### Finding Description
`requestWithdraw` branches on `defaultRecoveryFinalized`: [1](#0-0) 

The code only `_burn`s strategy tokens held by the CDO and `_mint`s an equal amount to the user. Unlike `collectWithdrawFunds` / `collectInstantWithdrawFunds`, no `safeTransferFrom` pulls underlying into the vault, and nothing increments `defaultRecoveryReserve`. The reserve was crystallized once in `finalizeDefaultRecovery`: [2](#0-1) 

`_claimPostDefaultWithdrawRequest` pays 1:1 from that same reserve: [3](#0-2) 

The comment "The CDO passes an already-haircut amount" only means `_amount` reflects the lowered virtualPrice — it does not add backing. Analogous to the CVE (a decode routine consuming a crafted archive with no backing data): a post-default claim consumes reserve with no corresponding deposit, so total claims exceed `reserveAmount`.

### Impact Explanation
Direct theft / insolvency of recovery funds. Concretely: attacker holds tranche tokens (or buys them cheaply post-default since tranche price collapsed). They call `requestWithdraw`, then `claimWithdrawRequest`, and receive `postDefaultRequests` underlyings at 1:1 from `defaultRecoveryReserve`. Every unit they withdraw reduces what is left for defaulted-epoch receipt holders (who are entitled to `claimBasis * defaultRecoveryPrice / 1e18`) and for the `DefaultDistributor` flow. If `defaultRecoveryReserve` is depleted, legitimate defaulted claims revert on transfer or pay out zero — permanent loss for earlier claimants equal to the attacker's extracted amount. With recoveryPrice e.g. 0.5, an attacker holding defaulted-epoch-basis tranches extracts double their fair share.

### Likelihood Explanation
Only requirements: hold or acquire tranche tokens after default finalization and call the same `requestWithdraw`/`claimWithdrawRequest` path any lender uses. No privileged role needed — the attacker is an ordinary tranche-token holder, which is in scope. Post-default tranche tokens trade near the recovery price, so acquiring claim basis cheaply is realistic. Guards present (`_onlyIdleCDO`, `_ensureDefaultRecoveryInitialized`, the revert on unclaimed funded receipts at line 249-251) do not check that post-default receipts are backed; the zero-funding gap is unprotected.

Uncertainty I could not fully resolve within available context: the exact body of `_transferDefaultRecovery` (whether it decrements `defaultRecoveryReserve` or reverts when underfunded) and whether `IdleCDOEpochVariant` supplies fresh underlying alongside post-default requests. If the CDO funds post-default requests from new borrower repayments routed into the reserve, the drain is bounded by those repayments — but even then an attacker front-running the reserve still dilutes honest defaulted claimants, since `postDefaultRequests` pays at par ahead of haircutted claims.

### Recommendation
Do not let post-default receipts draw from the finalized recovery reserve. Either (a) require post-default `requestWithdraw` to pull matching underlying into a segregated accounting bucket (e.g., increment a `postDefaultReserve` funded by actual borrower repayments and pay `postDefaultRequests` only from it), or (b) block `requestWithdraw` entirely once `defaultRecoveryFinalized` is true and route post-default exits through `DefaultDistributor.claim` at `defaultRecoveryPrice`. At minimum, add a check that `postDefaultRequests` payouts cannot push cumulative recovery disbursements past `defaultRecoveryReserve`.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPostDefaultRequestDrainsReserve() external {
    // 1. Lender deposits AA+BB, epoch runs, borrower defaults mid-epoch
    idleCDO.depositAA(10_000e18);
    _startEpochAndCheckPrices(0);
    // borrower default path
    cdoEpoch._handleBorrowerDefault();           // or finalizeDefault flow
    strategy.finalizeDefaultRecovery(recovered, recoverySource); // recoveryPrice < 1

    // 2. Attacker acquires tranche tokens post-default and requests withdraw
    uint256 attackerShares = AAtranche.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerShares, address(AAtranche)); // mints unfunded receipt

    uint256 reservePre = strategy.defaultRecoveryReserve();
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();             // _claimPostDefaultWithdrawRequest pays 1:1
    uint256 attackerGot = underlying.balanceOf(attacker);

    // 3. Honest defaulted-epoch receipt holder now claims
    vm.expectRevert(); // underfunded reserve -> transfer fails, or pays less than recoveryPrice*basis
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();

    assertGt(attackerGot, attackerShares * strategy.defaultRecoveryPrice() / 1e18);
    assertLt(strategy.defaultRecoveryReserve(), reservePre - attackerGot);
}
```

The broken invariant is receipt solvency: the sum of `_claimPostDefaultWithdrawRequest` payouts plus defaulted-epoch recovery payouts exceeds the reserve crystallized at finalization, because post-default receipts carry no funding.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
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
