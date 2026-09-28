### Title
Rebase yield on funded withdrawal claims is stranded in `IdleCreditVault` — claims pay only the snapshotted amount - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` pays withdrawal claims from fixed amounts snapshotted at request time (`withdrawsRequests`, `instantWithdrawsRequests`, `withdrawsRequestsByEpoch`). When the vault `token` is a rebasing asset (e.g., aToken-style or stETH-style balances that grow in `balanceOf`), the underlying held by the strategy to back funded claims grows, but no claimant can ever receive the growth. The rebase surplus has no owner in accounting and sits idle in the strategy contract; the only way it can move is `transferToken`, an `onlyOwner` rescue — i.e., yield that economically belongs to the claimants/LPs is siphoned out of the system or permanently stranded.

### Finding Description
Withdrawal economics are fully amount-based:

- `requestWithdraw` mints a receipt and records `withdrawsRequests[_user] += _amount` / `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` [1](#0-0) .
- `requestInstantWithdraw` records `instantWithdrawsRequests[_user] += _amount` [2](#0-1) .
- Funding is collected as an absolute amount: `collectWithdrawFunds(_amount)` and `collectInstantWithdrawFunds(_amount)` pull exactly `_amount` of underlying into the strategy [3](#0-2) .
- Claims transfer exactly the stored amount: `_claimFundedWithdrawRequest` → `_transferFundedClaim(_user, amount)` and `claimInstantWithdrawRequest` → `_transferFundedClaim(_user, amount)` [4](#0-3) .

There is no pro-rata distribution of the strategy's actual `underlyingToken.balanceOf(address(this))`. `_transferFundedClaim` even treats balance above `defaultRecoveryReserve` as spendable only up to `_amount`, leaving any rebase growth behind [5](#0-4) . The same fixed-basis pattern applies to default recovery: `finalizeDefaultRecovery` computes `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` once, and later rebases of the reserve are not credited to claimants who are paid `claimBasis * defaultRecoveryPrice` [6](#0-5) .

The stranded surplus is not counted anywhere: `IdleCDOCreditVault.getContractValue` only counts `strategyToken` balance (shares) plus the CDO's own `token` balance minus `unclaimedFees`, so underlying sitting in the strategy backing claims is invisible to NAV [7](#0-6) .

### Impact Explanation
If the credit vault's underlying is a rebasing token, every token unit held by `IdleCreditVault` to back funded withdrawal/instant claims and the default recovery reserve accrues rebase rewards that no user can claim. The surplus accumulates indefinitely and can only be extracted via `transferToken` (onlyOwner) [8](#0-7) . This is theft/permanent freezing of unclaimed yield proportional to `fundedFloat × rebaseRate × time` — e.g., with a 5%-yielding rebasing pool currency and pending claims outstanding across epoch delays, claimants lose all yield accrued between funding and claiming. Requires no privileged misbehavior; it is triggered simply by a rebasing underlying and normal epoch timing.

### Likelihood Explanation
Likelihood is conditional on deploying a credit vault whose `token` rebases in `balanceOf` rather than via exchange rate. Most production Pareto vaults use non-rebasing stablecoins (USDC/USDT), where the issue is latent. Withdrawal claims can sit funded for at least one full epoch (buffer + duration), so whenever it applies, accrual windows are long and the loss is systematic rather than attacker-dependent. Medium likelihood/low-medium magnitude in typical configs, hence medium.

### Recommendation
Track funded claims as shares of the strategy's claimable balance instead of absolute amounts: on funding, record `claimShares = amount / underlyingToken.balanceOf(strategy)` (or snapshot `totalFundedBasis` vs balance), and on claim pay `claimShares * currentBalance / totalClaimShares` pro-rata so rebase growth flows to entitled claimants. Apply the same share-based treatment to `defaultRecoveryReserve` rather than a fixed `defaultRecoveryPrice`. Alternatively, explicitly document that rebasing underlyings are unsupported and revert in `initialize` for tokens without a fixed `balanceOf` semantics.

### Proof of Concept
Foundry fork test sketch (mock rebasing token doubling `balanceOf` growth on demand, e.g., via `hardhat_setStorageAt` on stETH-style `beaconBalance` as in `test/integration/deprecated/lido/lidoCDO.js`):

```solidity
// test/foundry/RebaseYieldStuck.t.sol
function test_RebaseYieldOnFundedClaimsStuck() public {
    // 1. Deploy IdleCDOEpochVariant + IdleCreditVault with RebasingERC20 as `token`.
    // 2. KYC'd lender deposits 100_000e6 via depositAA during buffer; manager startEpoch.
    // 3. Lender calls requestWithdraw for full balance -> withdrawsRequests[user] = 100_000e6.
    // 4. Borrower repays; stopEpoch -> collectWithdrawFunds pulls 100_000e6 into strategy.
    // 5. Rebase: strategy underlying balance grows to 105_000e6.
    // 6. claimWithdrawRequest(user) pays exactly 100_000e6 (stored amount).
    uint256 claimed = vault.claimWithdrawRequest(user); // == 100_000e6
    assertEq(underlying.balanceOf(address(vault)), 5_000e6); // rebase yield stranded
    // 7. No user-facing function can claim the 5_000e6; only owner transferToken can move it.
}
```

The same sequence applies to `instantWithdrawsRequests` funded via `collectInstantWithdrawFunds` and to `defaultRecoveryReserve` after `finalizeDefaultRecovery` (rebase growth on the reserve is never claimable since `defaultRecoveryPrice` is fixed).

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L288-294)
```text
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L341-349)
```text
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L360-374)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L688-693)
```text
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L963-965)
```text
  function transferToken(address _token, uint256 value, address _to) external onlyOwner {
    IERC20Detailed(_token).safeTransfer(_to, value);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```
