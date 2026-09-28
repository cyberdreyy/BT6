### Title
Fee-on-transfer pool currency silently under-collateralizes strategy tokens and recovery reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` (the credit-vault strategy behind `IdleCDOCreditVault`) assumes every `safeTransferFrom` delivers exactly the requested `amount`. It mints strategy tokens 1:1 for the nominal amount in `deposit`, credits `pendingWithdraws`/`defaultRecoveryReserve` with nominal amounts in `collectWithdrawFunds`, `collectInstantWithdrawFunds` and `finalizeDefaultRecovery`, and pays receipts with nominal `safeTransfer` amounts in `_transferFundedClaim`/`_transferDefaultRecovery`. If the pool currency is (or becomes) a fee-on-transfer token — e.g. USDT, whose fee can be switched on retroactively for existing deployments — each pull delivers less than the booked amount. The CDO layer is already balance-delta aware (`IdleCDOCreditVault._deposit` mints tranche shares on `_contractTokenBalance(_token) - _preBal`), but the strategy layer is not, so strategy tokens are over-issued and claims become unbacked.

### Finding Description
Two distinct mismatches exist:

1. **Over-issued strategy tokens on deposit.** `IdleCDOCreditVault._deposit` pulls `_amount` from the user, mints tranche tokens only for the *actual* balance delta, and then calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the *nominal* amount. `IdleCreditVault.deposit` then does `underlyingToken.safeTransferFrom(msg.sender, address(this), _amount)` and `_mint(msg.sender, _amount)` at `contracts/strategies/idle/IdleCreditVault.sol:603-604`. The strategy receives `_amount - fee` but issues `_amount` strategy tokens to the CDO. The pull succeeds whenever the CDO holds residual underlying (fees accrued in `unclaimedFees`, buffer funds between epochs, or funds left by a failed borrower send), silently consuming the fee shortfall from other accounting buckets. `totEpochDeposits += _amount` also records the gross figure, so borrower funding math assumes more than the strategy holds.

2. **Over-credited recovery/funded reserves.** `collectWithdrawFunds` computes `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and pulls `_amount` (`lines 411-430`); `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` by `_amount` and pulls `_amount` (`lines 398-403`); `finalizeDefaultRecovery` sets `defaultRecoveryReserve = _recoveredAmount + prefundedReserve + ...` before pulling `_recoveredAmount` (`lines 686-708`). In all three, the booked backing equals the nominal parameter, not tokens received. Claims then pay `claimBasis * price / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements the reserve by the nominal payout while the actual balance is short by the cumulative inbound fees — and each outbound claim transfer itself charges another fee, so the claimant receives less than the reserve accounting consumed.

### Impact Explanation
Broken invariant: solvency — every issued strategy token and every unit of `defaultRecoveryReserve`/`lossRecoveryPrice` is assumed to be backed 1:1 by underlying. With a FoT token, the strategy's real balance falls short by the sum of all inbound fees plus all outbound claim fees. Early claimants redeem at par using other depositors' backing; once the aggregate deficit surfaces, later withdraw/instant/default-recovery claimants' transfers revert or underpay, permanently locking the residual share of funds in the strategy. The deficit grows unboundedly: `deficit ≥ Σ fees(deposits) + Σ fees(borrower funding pulls) + Σ fees(claim payouts)`.

### Likelihood Explanation
Requires the pool currency to take a fee on transfer. Credit vaults denominate in stablecoins; USDT's `basisPointsRate` can be enabled by Tether at any time *after* a vault is deployed, so existing pools are exposed without any code change. No privileged attacker is needed — ordinary deposits, epoch funding, and claims trigger the drift, and any unprivileged user can be the final claimant whose transfer fails. Note: a fresh vault with zero CDO buffer instead reverts inside `deposit` (pull exceeds the CDO's post-fee balance); the insolvency path materializes when the CDO holds residual underlying at deposit time, and the recovery-reserve mismatch (`collectWithdrawFunds`/`finalizeDefaultRecovery`) occurs regardless of buffer.

### Recommendation
Measure actual received amounts everywhere tokens move:

```sol
// deposit()
uint256 pre = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
uint256 received = underlyingToken.balanceOf(address(this)) - pre;
_mint(msg.sender, received);
totEpochDeposits += received;
```

In `IdleCDOCreditVault._deposit`, forward the measured delta (`_contractTokenBalance(_token) - _preBal`) to `strategy.deposit` instead of `_amount`. Apply the same pre/post balance measurement to `collectWithdrawFunds`, `collectInstantWithdrawFunds` and `finalizeDefaultRecovery`, and base `lossRecoveryPriceByEpoch`/`defaultRecoveryReserve`/`recoveryPrice` on received amounts. Alternatively, explicitly document and enforce that pool currencies must never take transfer fees.

### Proof of Concept
Foundry fork (mainnet, USDT as pool currency; prank Tether owner to enable `basisPointsRate`, or use an equivalent FoT ERC20 already live at the fork block):

```solidity
function testFoTDrainsClaims() public {
    // enable USDT transfer fee: prank owner, setParams(10, 20, 1)
    vm.prank(TETHER_OWNER);
    USDT.setParams(10, 20, 1); // 10 bps fee

    // 1. LP1 deposits 1_000_000e6 into AA during buffer
    depositAA(LP1, 1_000_000e6);
    //    CDO received 999_900e6 but IdleCreditVault minted 1_000_000e6 strategy tokens
    //    (pull succeeded using residual CDO balance, e.g. accrued fees/dust)

    // 2. run epoch, borrower repays principal + interest
    startAndStopEpochWithRepayment();

    // 3. LP1 and LP2 request withdraw; borrower funds collectWithdrawFunds(X)
    //    strategy books X into lossRecoveryPrice/pendingWithdraws but receives X - fee(X)

    // 4. claims
    claimWithdraw(LP1); // succeeds, paid from pooled backing
    vm.expectRevert();  // or underpayment: balance < reserve-adjusted amount
    claimWithdraw(LP2); // last claimant is short by accumulated fees
}
```

Assertions: `IdleCreditVault.balanceOf(idleCDO) > underlyingToken.balanceOf(strategy)` immediately after deposits; after `collectWithdrawFunds`, `underlyingToken.balanceOf(strategy) < pendingClaims * lossRecoveryPrice / 1e18`; final claim reverts on insufficient balance or transfers less than the receipt amount.