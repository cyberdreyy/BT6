### Title
`IdleCreditVault.deposit` and funding pulls credit the nominal amount instead of the balance actually received, breaking 1:1 strategy-token backing for fee-on-transfer underlyings - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.deposit` pulls `_amount` underlying from the IdleCDO and mints exactly `_amount` strategy tokens, without measuring the received balance. The same nominal-vs-received assumption exists in `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery`, which mark receipt claims as funded (or reserve accounted) at the requested `_amount` regardless of what actually arrived. If the vault underlying charges a transfer fee, strategy tokens (and funded receipt accounting) exceed real backing and the deficit is socialized onto the last claimers.

### Finding Description
The IdleCDO side of the deposit flow is already fee-aware: `IdleCDOCreditVault._deposit` snapshots the balance and mints tranche shares only for `_contractTokenBalance(_token) - _preBal`.

```solidity
// contracts/IdleCDOCreditVault.sol:202-206
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

But the strategy layer immediately undoes that protection by minting claims at the nominal `_amount`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:601-605
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_mint(msg.sender, _amount);
```

With a fee-on-transfer token, the CDO received `_amount - fee` from the depositor (and minted shares for that), yet the strategy mints `_amount` receipt tokens to the CDO — `fee` unbacked strategy tokens that later claim underlying 1:1 via `requestWithdraw`/`claimWithdrawRequest`. The identical pattern exists in:

- `collectWithdrawFunds` — clears `pendingWithdraws` (or sets `lossRecoveryPriceByEpoch` at par) using `_amount` while `safeTransferFrom` delivers less, so funded claims exceed the strategy balance.
- `collectInstantWithdrawFunds` — decrements `pendingInstantWithdraws` by `_amount` and transfers the nominal amount.
- `finalizeDefaultRecovery` — computes `reserveAmount`/`defaultRecoveryPrice` from nominal `_recoveredAmount` while pulling less.
- `IdleCDOEpochQueue.processDepositsToBorrower` / `deposit` — records `epochPendingDeposits` at nominal `amount` (queue is acknowledged-out-of-scope but shows the same class).
- `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` — pays `_user` `_underlyings - fee` assuming the full `_underlyings` arrived.

### Impact Explanation
Each affected deposit/funding leg creates `transferFee` units of unbacked claim basis while accounting reports full backing. When withdraw receipts are claimed (`_transferFundedClaim`, `_transferDefaultRecovery`), earlier claimers are paid at par and the cumulative shortfall reverts on the final claimants — permanent freezing of up to the aggregate fee amount of unclaimed funds. In the queued/prefunded flow the borrower receives less than the face value of minted debt, silently shifting the fee onto the pool unless the borrower overpays. The loss is quantified as `feeRate * amount` per funding event, compounding across epochs.

### Likelihood Explanation
It requires the vault's `token` to deduct a transfer fee (e.g., USDT with fee enabled, PAXG-class tokens). Deployed credit vaults use stablecoins, which is why this is medium likelihood rather than high, but the strategy/`token` is configurable at `initialize` and no guard rejects fee-on-transfer assets. The attacker path is unprivileged: any KYC-passed lender depositing through `depositAA`/`depositBB` or the queue triggers a strategy mint exceeding received underlying.

### Recommendation
Measure received amounts instead of trusting call parameters:

```solidity
uint256 before = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
_mint(msg.sender, underlyingToken.balanceOf(address(this)) - before);
```

Apply the same before/after balance pattern in `collectWithdrawFunds`, `collectInstantWithdrawFunds`, and `finalizeDefaultRecovery` (compute `lossRecoveryPrice`/`defaultRecoveryPrice` from the received delta), and in `IdleCDOCreditVault._deposit` pass the received amount (`balanceAfter - _preBal`) to `IIdleCDOStrategy(strategy).deposit(...)` rather than `_amount`.

### Proof of Concept
Foundry test sketch (fee token = 1% burn on transfer):

```solidity
// test/foundry/FeeOnTransferDeposit.t.sol
function testFotMintMismatch() public {
    FeeToken usdt = new FeeToken(); // burns 1% on transfer/transferFrom
    // deploy IdleCreditVault + IdleCDOCreditVault with `usdt` as token
    vm.prank(alice);
    usdt.approve(address(cdo), 1000e6);
    // CDO mints shares for 990e6 (received), strategy mints 1000e6 tokens to CDO
    cdo.depositAA(1000e6);
    // strategy holds 990e6 (before forwarding to borrower), yet
    // balanceOf(cdo) == 1000e6 at price() == 1e6 -> NAV overstated by 10e6
    assertEq(strategy.balanceOf(address(cdo)), 1000e6);
    assertEq(usdt.balanceOf(address(strategy)), 990e6); // claim > backing
}
```

Note: the deposit path partially self-mitigates when the CDO's residual balance is insufficient for the strategy's `transferFrom` of nominal `_amount` (the call reverts, producing a deposit DoS). The insolvency materializes wherever the pull succeeds — queued/prefunded deposits, `collectWithdrawFunds` after borrower repayment to the CDO, and recovery funding — which is where the balance-delta fix is required.