### Title
`IdleCreditVault` accounts for gross transfer amounts, creating a reserve shortfall under fee-on-transfer underlyings - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The external report describes a contract that transfers `_amount` of a token but assumes it received exactly `_amount`, breaking when the token charges a transfer fee. `IdleCDO`/`IdleCDOCreditVault` handle this correctly for the LP-facing deposit: `_deposit` mints shares from `_contractTokenBalance(_token) - _preBal`, i.e. the actual received amount. However, the credit-vault strategy `IdleCreditVault` reintroduces the same bug class on every downstream pull: `deposit`, `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery` all `safeTransferFrom` a gross `_amount` and credit/mint/reserve that full amount without measuring what actually arrived.

### Finding Description

All four funding paths assume `balance received == _amount`:

1. `deposit(uint256 _amount)` pulls `_amount` from the CDO and mints `_amount` strategy tokens 1:1 (`contracts/strategies/idle/IdleCreditVault.sol:596-617`). With a fee-on-transfer underlying the strategy receives `_amount - fee` but mints `_amount`, inflating the strategy-token supply beyond backing.
2. `collectWithdrawFunds(uint256 _amount)` decrements `pendingWithdraws` by the funded amount and pulls `_amount` from the CDO (lines 411-430). The vault receives less than recorded.
3. `collectInstantWithdrawFunds(uint256 _amount)` decrements `pendingInstantWithdraws` and pulls the same gross amount (lines 398-403).
4. `finalizeDefaultRecovery(uint256 _recoveredAmount, ...)` pulls `_recoveredAmount` and folds it into `defaultRecoveryReserve` at full value (lines 661-710), while `defaultRecoveryPrice` is computed as `reserveAmount * RECOVERY_FULL / totalBasis` on that inflated reserve.

The corresponding payout side (`_transferFundedClaim`, `_transferDefaultRecovery`) pays claims at par or at `defaultRecoveryPrice`, debiting the reserve by the accounted amount, not the received amount.

### Impact Explanation

Solvency / "one receipt one payout" invariant is broken. Each funded unit is short by the token's transfer fee, so the last claimants — either funded withdraw-receipt holders (`_claimFundedWithdrawRequest` → `_transferFundedClaim`), instant-withdraw claimants (`claimInstantWithdrawRequest`), or post-default recovery claimants (`_claimPostDefaultWithdrawRequest`, `_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) — have their claims revert on `safeTransfer` for an amount up to roughly `fee% * totalFunded`. For `finalizeDefaultRecovery`, the deficit is baked into `defaultRecoveryPrice`, so the reserve is permanently under-collateralized and the tail of recovery claims is permanently unpayable. Loss is quantified as the aggregate transfer-fee fraction of all funds pulled through these paths. The `deposit` path additionally mints unbacked strategy tokens, overstating `balanceOf(idleCDO)` and thus active NAV / loss bases used by `_lossActiveBasis` and default finalization.

### Likelihood Explanation

Requires the vault's `token` (the pool currency set in `initialize`) to charge a transfer fee. Credit vaults target stables such as USDT, which embeds an (owner-toggleable) basis-point fee that can be enabled after vault deployment — no privileged action inside this protocol is needed. Once enabled, every `stopEpoch` funding call, instant-withdraw collection, and default-recovery finalization accrues the silent shortfall; no existing guard (skim, reserve check in `_transferFundedClaim`, `onlyIdleCDO`, epoch gating) detects the mismatch because none of these paths compare pre/post balances. The attacker surface is trivial — any holder simply claims; the deficit is socialized onto whoever claims last.

### Recommendation

Measure actual received amounts on every pull into the strategy, mirroring the fix in `_deposit`:

```solidity
// deposit()
uint256 _pre = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_amount = underlyingToken.balanceOf(address(this)) - _pre;
_mint(msg.sender, _amount);
```

Apply the same balance-delta pattern in `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery` (reduce `pendingWithdraws`/`pendingInstantWithdraws`/`defaultRecoveryReserve` by the received amount), and make `sendInterestAndDeposits` receivers aware that the CDO's balance increase may be less than `_amount`.

### Proof of Concept

Foundry fork test outline (e.g. extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Deploy a mock USDT-style token with a 1% transfer fee, or fork USDT and
// toggle its basisPointsRate via owner prank.
FeeToken usdt = new FeeToken(100 /* 1% */);
vault.initialize(address(usdt), owner, manager, borrower, "B", apr);
cdo deposit path:
  // user deposits X; CDO receives X*99/100, mints shares for received amount (correct)
  // but cdo then calls vault.deposit(X): vault pulls X from CDO -> reverts (insufficient
  // balance) OR, if CDO held a dust buffer, vault receives X*99/100 yet mints X strategy
  // tokens -> assert(strategy.balanceOf(cdo) > strategy.underlyingBalance())
claim path:
  // borrower funds pendingWithdraws = P via stopEpoch; collectWithdrawFunds pulls P,
  // vault receives P*99/100 but pendingWithdraws clears P
  // last claimant calling claimWithdrawRequest -> _transferFundedClaim reverts;
  // assert shortfall == P * 1 / 100
recovery path:
  // finalizeDefaultRecovery(R, source): reserve credited R, received R*99/100;
  // recoveryPrice = R * RECOVERY_FULL / basis computed on R -> tail claims revert,
  // permanent freeze of ~1% of recovery reserve
```

Caveat: I verified the deposit/mint paths and claim accounting in `IdleCreditVault.sol` directly, but could not read `IdleCDOEpochVariant.stopEpoch` in the available iterations to confirm the exact sequencing of borrower funding into `collectWithdrawFunds`; the shortfall mechanics above hold regardless of which CDO function triggers the pulls, since the strategy itself records the gross `_amount`.