### Title
Funded and unfunded instant-withdraw receipts share one aggregate ledger and are paid out at par — early claimers drain funds earmarked for other receipt holders - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The CVE analog is a shared buffer/descriptor reused across jobs so that outputs of several jobs get mixed. In `IdleCreditVault`, the same reuse exists at the receipt level: `instantWithdrawsRequests[_user]` aggregates instant withdraw receipts across epochs without tracking how much of that aggregate was actually funded by the borrower, and `claimInstantWithdrawRequest` pays the entire aggregate at par. Funding is tracked only in the aggregate counters `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch`, which the claim path never consults. One user's unfunded receipt can therefore be paid out of underlying that was collected to back other users' receipts — exactly the "mixed outputs from a reused channel" bug class.

### Finding Description

`requestInstantWithdraw` mints receipt strategy tokens and increments three ledgers (`instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`, `pendingInstantWithdraws`) [1](#0-0) . Funding arrives separately via `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` — no per-epoch or per-user funded amount is recorded [2](#0-1) .

`claimInstantWithdrawRequest` then pays the whole `instantWithdrawsRequests[_user]` balance through `_transferFundedClaim`, whose only guard is the `defaultRecoveryReserve` isolation check — it does not verify that this user's receipts, or even this epoch's receipts, were funded [3](#0-2) [4](#0-3) .

The contract itself acknowledges that partial instant funding is a real state: `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` as "current instant claims already backed by strategy underlyings" [5](#0-4) . That partially-funded state can exist before any default finalization, and in it the claim function has no funded/unfunded distinction — the same ledger is reused for both classes of receipts and pays them all from one pool.

### Impact Explanation

Attacker path (unprivileged lender): during a running epoch with instant withdrawals enabled, the attacker requests an instant withdraw of amount X. The honest CDO/borrower sequence funds only part of the instant bucket (e.g., because `startEpoch` moved limited cash to the strategy — the exact scenario `_defaultPrefundedInstantReserve` describes), or funds a different user's request while the attacker's remains pending. The attacker then calls `IdleCDOEpochVariant.claimInstantWithdrawRequest()` (gated only by `allowInstantWithdraw` [6](#0-5) ), which burns his entire receipt balance and transfers the full amount at par. The paid underlying comes from the shared strategy balance that was collected to satisfy other users' normal withdraw claims (`collectWithdrawFunds`) or other instant receipts. Direct theft equal to the unfunded portion of his receipt; the victims' later claims revert or pay less — a broken "one receipt, one funded payout" invariant and effective insolvency for the remaining claimants.

### Likelihood Explanation

Requires instant withdrawals enabled (`allowInstantWithdraw`) and a partially funded instant bucket, which the code explicitly models as reachable (partial prefunding, failed startEpoch transfers). No privileged misbehavior is needed — the honest manager/borrower funding sequence around `collectInstantWithdrawFunds` is sufficient. First-come-first-served claim ordering makes extraction reliable once the state exists. One caveat: I did not fully trace the CDO-side instant funding path (`getInstantWithdrawFunds`/`collectInstantWithdrawFunds` call sites in `IdleCDOEpochVariant`), so the exact conditions producing a persistent partially-funded bucket during normal (non-default) operation should be confirmed; the accounting gap on the strategy side is unconditional.

### Recommendation

Track funded instant receipts per epoch (mirroring `withdrawsRequestsByEpoch`/`lossRecoveryPriceByEpoch`): when `collectInstantWithdrawFunds` runs, record the funded amount per epoch, and in `claimInstantWithdrawRequest` pay only the funded portion of each epoch's basis, leaving the unfunded remainder in the ledger for default/recovery handling. Alternatively, cap the par payout at the per-user funded share derived from `pendingInstantWithdraws` vs `instantWithdrawClaimsByEpoch[epochNumber]`.

### Proof of Concept

Foundry fork PoC sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testInstantClaimMixesUnfundedReceipts() external {
    // epoch running, allowInstantWithdraw == true, APR mode irrelevant
    address alice = makeAddr("alice");
    address bob   = makeAddr("bob");

    // both deposit and request instant withdraws in the same epoch
    uint256 a = 100 * ONE_SCALE;
    uint256 b = 100 * ONE_SCALE;
    _instantWithdrawRequestFor(alice, a); // requestInstantWithdraw via cdoEpoch
    _instantWithdrawRequestFor(bob, b);

    // honest sequence: strategy ends up holding only `a` underlying for the
    // instant bucket (partial funding / startEpoch partial move)
    // pendingInstantWithdraws == a + b, balance == a (+ any normal funded claims)

    // bob claims first: ledger pays his FULL b at par despite his receipt
    // being the unfunded one -> drains alice's funded backing
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(bob); // succeeds, transfers b

    // alice's funded claim now reverts on insufficient balance or is short-paid
    vm.prank(address(cdoEpoch));
    vm.expectRevert();
    strategy.claimInstantWithdrawRequest(alice);
}
```

The broken invariant is receipt–funding correspondence: `claimInstantWithdrawRequest` trusts the aggregate receipt ledger as fully funded, so mixed funded/unfunded receipts pay out of one shared underlying pool.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L975-978)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
```
