### Title
Gross-amount accounting after `transferFrom` breaks on fee-on-transfer underlyings: unbacked strategy tokens minted in `depositDuringEpoch`/strategy `deposit` and spurious borrower default at `stopEpoch` - (File: contracts/IdleCDOEpochVariant.sol, contracts/IdleCDOCreditVault.sol, contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is "code assumes the amount received equals the amount passed to `transferFrom`". `IdleCDO._deposit` and `IdleCDOCreditVault._deposit` correctly mint tranche shares from the post-transfer balance delta (`_contractTokenBalance(_token) - _preBal`), but everything downstream still operates on the gross `_amount`: the strategy pull `IIdleCDOStrategy(strategy).deposit(_amount)` in `IdleCDOCreditVault.sol:211`, and in `IdleCDOEpochVariant.depositDuringEpoch` (`IdleCDOEpochVariant.sol:685-732`) the minted shares, `mintStrategyTokens(_amount)`, and the forward `_transferUnderlyings(_borrower(), _amount)` all use the requested amount rather than the received amount. With a fee-on-transfer underlying, every one of those steps overstates the funds actually moved, corrupting NAV/strategy-token backing or reverting the pull — and a revert inside the `try this.getFundsFromBorrower(...)` path of `_stopEpoch` (`IdleCDOEpochVariant.sol:408-505`) is treated as a borrower default.

### Finding Description
1. **`IdleCDOCreditVault._deposit`** (`contracts/IdleCDOCreditVault.sol:202-211`): the CDO pulls `_amount` from the user, receives `_amount - fee`, mints tranche shares on the delta (correct), then calls `strategy.deposit(_amount)` with the gross amount. `IdleCreditVault.deposit` pulls `_amount` underlying from the CDO and mints strategy tokens for that amount. Two outcomes depending on the CDO's balance:
   - If the CDO holds exactly what it received (`_amount - fee`), the strategy's `safeTransferFrom` reverts → deposit DoS.
   - If the CDO holds a buffer balance (e.g., pending withdrawals or prior liquidity during the buffer phase), the strategy pulls the full `_amount`, receiving `_amount - fee` after the second fee, yet mints `_amount` of strategy-token backing → unbacked strategy tokens inflate `getContractValue()` by the fee amount, and the deficit is socialized across tranche holders.

2. **`depositDuringEpoch`** (`IdleCDOEpochVariant.sol:685-732`): pulls `_amount`, then computes `_minted` from `_amount + trancheInterest`, calls `IdleCreditVault(strategy).mintStrategyTokens(_amount)` and transfers the full `_amount` to the borrower. The contract only received `_amount - fee`, so the transfer to the borrower either reverts (mid-epoch deposits permanently broken) or, if any residual balance exists, drains funds belonging to other accounting buckets while minting shares/strategy tokens for value that never arrived.

3. **Spurious default on repayment** (`IdleCDOEpochVariant.sol:550-553`, `:408-505`, `:566-573`): `getFundsFromBorrower` does `_transferUnderlyingsFrom(_borrower(), address(this), _amount)` and `getInstantWithdrawFunds`/`_stopEpoch` then push fixed amounts onward via `collectWithdrawFunds(_pendingWithdraws)` / `collectInstantWithdrawFunds(_instantWithdraws)`. With a fee-on-transfer token the CDO receives less than requested, the subsequent outbound transfer of the full recorded amount reverts, the `catch` block calls `_handleBorrowerDefault`, and the vault enters the `defaulted` state — `isEpochRunning` cleared, withdraw requests disabled, pool paused — even though an honest borrower repaid in full. Funds are then frozen until the privileged `finalizeDefault` recovery flow, and recovery math is computed against a "default" that never happened.

### Impact Explanation
- Inflated strategy-token supply vs. actual underlying backing: direct insolvency, socialized across AA/BB holders (loss waterfall broken because NAV is overstated before losses are attributed).
- Permanent freezing of user funds: a normal deposit or a fully repaid epoch reverts or forces `defaulted = true`, disabling `requestWithdraw`, `claimWithdrawRequest`, and `depositAA/BB` until governance runs the hard-default finalization path.
- Loss magnitude equals the transfer fee on each affected flow (e.g., USDT-class fee of ~0.1–1%), plus full TVL freezing when `stopEpoch`/`getInstantWithdrawFunds` triggers the false default.

### Likelihood Explanation
Requires the vault's `token` to charge a fee on transfer. Deployed Idle vaults use non-fee stablecoins, so the likelihood is conditional on the token — the same conditional assumption as the source issue (rated Medium). No guard mitigates it: `_skimDonatedAssets` only sends *excess* balance to `feeReceiver` and does not reconcile shortfalls; `requestWithdraw`, `depositDuringEpoch`, `stopEpoch`, and `getInstantWithdrawFunds` all assume received == requested. The attacker need not do anything — a KYC'd lender depositing, or the honest borrower repaying, triggers the corruption. Severity: Medium.

### Recommendation
- In `depositDuringEpoch`, measure `received = _contractTokenBalance(token) - _preBal` after `_transferUnderlyingsFrom` and use `received` for the minted-share computation, `mintStrategyTokens`, and the borrower transfer — mirroring the delta pattern already used in `_deposit`.
- In `IdleCDOCreditVault._deposit` / `IdleCDO._deposit`, pass the actually received delta to `IIdleCDOStrategy(strategy).deposit(...)` instead of `_amount`.
- In `getFundsFromBorrower`/repayment paths, check the post-pull balance delta and, if it is short, treat the shortfall explicitly rather than letting the downstream fixed-amount transfer revert into `_handleBorrowerDefault`; alternatively document that fee-on-transfer underlyings are unsupported and enforce a `received == _amount` check at deposit entry points so incompatible deployments fail fast rather than silently minting unbacked shares.

### Proof of Concept
Foundry test against a fee-on-transfer ERC20 mock (2% burn on transfer) wired as `token` of an `IdleCDOEpochVariant` + `IdleCreditVault` deployment:

```solidity
// test/foundry/FeeOnTransferDeposit.t.sol
function test_depositDuringEpoch_feeToken_mintsUnbacked() public {
    // setup: epoch running, isDepositDuringEpochDisabled = false, AA supply seeded
    uint256 amount = 1000e6;
    feeToken.mint(lender, amount);
    vm.startPrank(lender);
    feeToken.approve(address(cdo), amount);
    // CDO receives 980, but mints shares/strategyTokens for 1000
    // and tries to forward 1000 to borrower -> reverts (DoS),
    // or if CDO had a residual buffer, strategyToken supply is
    // inflated by 20 vs real backing.
    cdo.depositDuringEpoch(amount, AA); 
    vm.stopPrank();
}

function test_stopEpoch_feeToken_spuriousDefault() public {
    // epoch running; borrower repays _expectedInterest + _pendingWithdraws in full
    // CDO receives amount-fee; collectWithdrawFunds(pendingWithdraws) reverts;
    // catch -> _handleBorrowerDefault -> defaulted == true
    vm.prank(manager);
    cdo.stopEpoch(0, 0);
    assertTrue(cdo.defaulted());        // honest repayment misread as default
    // withdrawals now frozen: requestWithdraw reverts, claims blocked
}
```

Uncertainty note: the exact internals of `IdleCreditVault.deposit` (whether it mints strategy tokens from the `_amount` argument or a balance delta) could not be fully confirmed from the indexed snippets; the `depositDuringEpoch` path, however, unambiguously mints and forwards the gross `_amount`, so the core finding stands regardless.