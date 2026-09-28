### Title
Fee-on-transfer tokens cause share over-minting and insolvency in `depositDuringEpoch` / `IdleCDOEpochQueue.requestDeposit` — ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
The regular deposit path `_deposit` correctly measures the *actual* tokens received (`_contractTokenBalance(_token) - _preBal`) before minting tranche shares. However, `depositDuringEpoch` in `IdleCDOEpochVariant`, `IdleCDOEpochQueue.requestDeposit`, and `IdleCreditVault.deposit` all credit/account the *stated* `_amount` rather than the received amount. With a fee-on-transfer (or burn-on-transfer) underlying, an attacker mints shares for tokens never delivered, and the vault forwards the full stated amount to the borrower, draining the buffer that backs other depositors' claims.

### Finding Description
In `IdleCDO._deposit` (and the credit-vault override), shares are minted on the measured received amount:

```solidity
// contracts/IdleCDO.sol:250-253
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

`depositDuringEpoch` does the opposite — it pulls `_amount`, then mints shares and forwards funds based on the unmeasured `_amount`:

```solidity
// contracts/IdleCDOEpochVariant.sol:685,724,730,732
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
IdleCreditVault(strategy).mintStrategyTokens(_amount);
_transferUnderlyings(_borrower(), _amount);
```

If `token` is fee-on-transfer and the vault receives `_amount * (1 - f)`, the vault:
1. mints tranche shares priced on full `_amount + trancheInterest`,
2. mints `_amount` strategy tokens via `mintStrategyTokens` (which blindly mints: `strategies/idle/IdleCreditVault.sol` `_mint(msg.sender, _amount)`),
3. transfers the full `_amount` to the borrower — paying out `f * _amount` more than it received, taken from the buffer that secures pending withdraw requests and pre-epoch deposits.

The same stated-vs-received flaw exists in `IdleCDOEpochQueue.requestDeposit` (`IdleCDOEpochQueue.sol:121-126`): the queue records `userDepositsEpochs` and `epochPendingDeposits` at the stated `amount`, then `processDepositsToBorrower` (`:179`) pushes the full `_pending` to the borrower — again overpaying by the aggregate fee, funded by other users' queued deposits, and minted claims exceed actual assets.

`IdleCreditVault.deposit` (`strategies/idle/IdleCreditVault.sol:601-605`) also does `safeTransferFrom(... _amount); _mint(msg.sender, _amount)` — strategy tokens minted on the stated amount during the epoch-boundary deposit flow.

### Impact Explanation
Each deposit during a running epoch with a fee-on-transfer underlying creates a structural shortfall of `fee * amount`: shares and strategy tokens are issued against non-existent assets, and the shortfall is immediately paid to the borrower out of vault-held funds earmarked for pending withdrawals and other depositors. This is a direct solvency break — last withdrawers/claimants cannot be paid in full (permanent freezing of the tail of `claimWithdrawRequest`/`claimInstantWithdrawRequest` payouts), or the attacker can cycle deposits to extract the buffer: deposit `amount` FoT tokens, vault sends `amount` to borrower, attacker later claims shares backed by more than they contributed.

### Likelihood Explanation
Requires the pool's underlying to be fee-on-transfer or deflationary-on-transfer. Deployed pools use standard stablecoins, but the codebase is generic over `token` (any `IERC20Detailed`), and tokens like USDT support togglable fees; new pools on other chains could use FoT underlyings. The attacker only needs `isWalletAllowed` (KYC-passing lender) and a running epoch with `isDepositDuringEpochEnabled` (non-programmable, non-AYS, fixed-APR mode — the exact mode where `depositDuringEpoch` is permitted). The mid-epoch deposit path is precisely where the received-amount measurement was dropped relative to `_deposit`.

### Recommendation
Measure the actual received amount in `depositDuringEpoch`, matching `_deposit`:

```solidity
uint256 _preBal = _contractTokenBalance(token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_amount = _contractTokenBalance(token) - _preBal;
```

and use the measured `_amount` for interest calc, `_mintShares`, `mintStrategyTokens`, and the borrower transfer. Apply the same fix to `IdleCDOEpochQueue.requestDeposit` (record received amount in `userDepositsEpochs`/`epochPendingDeposits`) and `IdleCreditVault.deposit` (mint strategy tokens on received amount). Alternatively, explicitly document/enforce that fee-on-transfer underlyings are unsupported (e.g., revert if received != stated, which is the safest check and also catches rebase quirks).

### Proof of Concept
Foundry test sketch (mock FoT token charging 1% on transfer, or fork a fee-enabled token):

```solidity
// Assume: vault in fixed-APR mode, epoch running, attacker KYC-allowed.
// token T burns 1% on every transfer.
uint256 amount = 1_000_000e6;

// 1. Honest depositors seed the pool pre-epoch (buffer holds their funds).
// 2. Epoch starts (startEpoch). Vault retains buffer liquidity.

// 3. Attacker calls depositDuringEpoch during the running epoch:
feeToken.approve(address(cdo), amount);
cdo.depositDuringEpoch(amount, AA);

// Received by vault: 990_000e6. But:
//  - shares minted priced on 1_000_000e6 + trancheInterest
//  - mintStrategyTokens(1_000_000e6)
//  - _transferUnderlyings(borrower, 1_000_000e6)  // pulls 10_000e6 from buffer

assertEq(feeToken.balanceOf(address(borrower)), amount);          // full amount forwarded
assertEq(feeToken.balanceOf(address(cdo)), seedBuffer - 10_000e6); // buffer drained by fee

// 4. At epoch end the attacker redeems shares valued at >= 1_000_000e6 + interest
// while only 990_000e6 was contributed -> direct profit equal to the fee taken
// from other depositors' funds; pending withdraw claimants are short.
```

The invariant broken is fair mint/burn plus solvency: shares minted must be fully backed by underlying received; here they are backed by stated input, and the overpayment is socialized onto the buffer.