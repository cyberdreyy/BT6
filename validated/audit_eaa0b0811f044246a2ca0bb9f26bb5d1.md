### Title
Post-default withdraw requests drain `defaultRecoveryReserve`, stealing recovery funds earmarked for defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
After `finalizeDefaultRecovery`, the vault keeps `defaultRecoveryReserve` as an isolated pool sized to exactly cover all defaulted-epoch receipt claims at `defaultRecoveryPrice`. The post-default `requestWithdraw` path mints a receipt and registers `postDefaultRequests` **without transferring any underlying into the strategy**, yet `_claimPostDefaultWithdrawRequest` pays those requests 1:1 by decrementing and spending `defaultRecoveryReserve`. Two unsynchronized claim flows (defaulted-epoch claims and post-default claims) mutate the same shared reserve, so post-default claims consume collateral that belongs to earlier claimants — the analog of operating on a shared object without proper locking/accounting.

### Finding Description
`finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and prices it against `totalBasis` (active LPs + defaulted pending receipts). The reserve is therefore exactly sized for existing claims; nothing remains for future ones.

Then `requestWithdraw` post-default branch:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol ~L247-257
if (defaultRecoveryFinalized) {
  if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
    revert NotAllowed();
  }
  _burn(msg.sender, _amount);      // burns CDO strategy tokens
  _mint(_user, _amount);           // mints user receipt
  postDefaultRequests[_user] = _amount;
  return;                          // NOTE: no underlying transferred in
}
```

And the claim:

```solidity
// ~L760-767
function _claimPostDefaultWithdrawRequest(address _user) internal returns (uint256 amount) {
  amount = postDefaultRequests[_user];
  if (amount == 0) return amount;
  postDefaultRequests[_user] = 0;
  _burn(_user, amount);
  _transferDefaultRecovery(_user, amount); // defaultRecoveryReserve -= amount
}
```

`_transferDefaultRecovery` decrements `defaultRecoveryReserve` and pays out of the strategy's token balance, which post-finalization consists solely of the recovery reserve. There is no code path that adds backing for `postDefaultRequests` (compare `collectWithdrawFunds`/`collectInstantWithdrawFunds`, which pull underlying from the CDO for normal claims). The burned CDO strategy tokens release value on the CDO side, not here.

### Impact Explanation
A user (any KYC-passed lender / tranche holder acting through the CDO withdraw path) who opens a post-default withdraw request can claim it at 1:1 from `defaultRecoveryReserve`, while defaulted-epoch claimants are only entitled to `claimBasis * defaultRecoveryPrice`. Each post-default claim directly reduces the reserve below the amount required to honor all finalized recovery claims, so the last defaulted-epoch claimants' `claimWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest` revert on insufficient balance or the reserve accounting underflows — direct theft and permanent freezing of unclaimed recovery yield. Loss is bounded by `defaultRecoveryReserve`, i.e. up to 100% of recovered funds.

### Likelihood Explanation
Requires the borrower-default + `finalizeDefaultRecovery` state to be reached, which is an operational/exceptional state rather than the common path; however once in it, any user able to route a withdraw request through the CDO can trigger the drain with a single sequence of calls (request → claim). The `_hasWithdrawRequest` gate only prevents stacking multiple requests per user, not first-come draining by any user.

Caveat: I could not fully verify within IdleCDOEpochVariant whether the CDO side pre-funds post-default requests into the strategy before calling `requestWithdraw`. If such a funding transfer exists and also credits `defaultRecoveryReserve`, the finding would be mitigated; the strategy-side code shown provides no such crediting, and `reserveDefaultRecovery` explicitly reverts once `defaultRecoveryFinalized` is set.

### Recommendation
Either (a) fund post-default receipts before registration — pull the underlying from the CDO inside the `defaultRecoveryFinalized` branch of `requestWithdraw` and track it in a separate bucket paid via `_transferFundedClaim` — or (b) increase `defaultRecoveryReserve` by the haircut-appropriate amount when a post-default request is registered. Post-default claims must never decrement the reserve earmarked for defaulted-epoch claimants.

### Proof of Concept
Foundry fork PoC outline (against a live IdleCDOEpochVariant + IdleCreditVault deployment):

```solidity
// test/foundry/PostDefaultReserveDrain.t.sol
function test_PostDefaultClaimDrainsRecoveryReserve() public {
    // 1. Reach defaulted + finalized state:
    //    - epoch running, borrower fails to repay
    //    - call CDO._handleBorrowerDefault / finalizeDefaultRecovery with
    //      recoveredAmount R and totalBasis B => defaultRecoveryReserve = R
    // 2. Victim V has a defaulted-epoch pending receipt worth claimBasisV * price
    // 3. Attacker A (KYC lender) calls CDO withdraw -> vault.requestWithdraw(amount, A, principal)
    //    postDefaultRequests[A] = amount; no underlying moved
    // 4. A calls CDO claim -> vault.claimWithdrawRequest(A)
    //    _claimPostDefaultWithdrawRequest pays `amount` and defaultRecoveryReserve -= amount
    // 5. V calls claim -> vault balance < required payout -> revert / unpayable
    assertEq(vault.defaultRecoveryReserve(), R - amount);   // reserve drained
    assertGt(amount * RECOVERY_FULL / vault.defaultRecoveryPrice(), 0); // A paid above haircut
    vm.expectRevert();
    vault.claimWithdrawRequest(V);                          // victim frozen
}
```