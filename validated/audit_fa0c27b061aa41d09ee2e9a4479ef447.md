### Title
`depositDuringEpoch` credits and forwards the full `_amount` for fee-on-transfer tokens, minting excess shares and draining the vault - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.depositDuringEpoch` is the one production deposit path that trusts the user-supplied `_amount` instead of measuring the actual balance delta. While `IdleCDO._deposit` and `IdleCDOCreditVault._deposit` mint shares using `_contractTokenBalance(_token) - _preBal`, `depositDuringEpoch` transfers `_amount` in, mints tranche shares priced on `_amount`, calls `IdleCreditVault.mintStrategyTokens(_amount)`, and then forwards the full `_amount` to the borrower. With a fee-on-transfer underlying, the vault receives less than `_amount` but pays out and accounts for the whole `_amount`, creating a deficit absorbed by other depositors' funds and an over-mint of tranche tokens.

### Finding Description
In `depositDuringEpoch`:

1. `_transferUnderlyingsFrom(msg.sender, address(this), _amount)` pulls tokens; if the token charges a transfer fee `f`, the vault actually receives `_amount - f` (contracts/IdleCDOEpochVariant.sol:685).
2. Shares are minted off the nominal amount: `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal` (line 724), so the attacker is credited as if `_amount` arrived.
3. `IdleCreditVault(strategy).mintStrategyTokens(_amount)` mints strategy tokens for the full amount (line 730), inflating `getContractValue()`-relevant accounting.
4. `_transferUnderlyings(_borrower(), _amount)` sends the full `_amount` to the borrower (line 732). Since only `_amount - f` arrived, the extra `f` is paid out of tokens already sitting in the vault (prior buffer-period deposits awaiting transfer, or withdraw liquidity), i.e., other depositors' principal.

The `_skimDonatedAssets()` call at line 679 happens *before* the pull, so it cannot correct the shortfall. The minted shares entitle the attacker to `_amount + trancheInterest` at epoch end, while the vault's real claim on the borrower only grew by `_amount` minted via `mintStrategyTokens` against `_amount - f` actually contributed — the gap `f` is socialized onto honest depositors through the NAV/waterfall accounting (`lastNAVAA`/`lastNAVBB`), and repeated deposits can exceed idle vault liquidity, at which point the borrower transfer reverts and `depositDuringEpoch` becomes unusable until the deficit is covered.

The same pattern exists in `IdleCDOEpochQueue.requestDeposit` (records `amount` without a balance delta at contracts/IdleCDOEpochQueue.sol:121-126), but the queue contract is out of scope, so the core finding is `depositDuringEpoch`.

### Impact Explanation
Each deposit of `_amount` with fee `f`:
- Mints tranche shares redeemable for `_amount + trancheInterest` against only `_amount - f` of real inflow — direct over-credit/theft of `f` plus prorated interest per deposit from other AA/BB holders.
- Pays `_amount` to the borrower from vault liquidity that only increased by `_amount - f`, draining `f` per call from funds belonging to other depositors or earmarked for withdraw requests; once idle liquidity is exhausted the call reverts (temporary freezing of the deposit path and griefing of withdraw funding).

Loss is bounded by `sum(f)` over attacker deposits; repeatable up to the vault's liquid balance / TVL limit, so it scales to full draining of un-deployed liquidity.

### Likelihood Explanation
Requires the credit vault's `token` to be a fee-on-transfer ERC20 (or a rebasing/transfer-tax wrapper configured by governance). `depositDuringEpoch` requires `isWalletAllowed(msg.sender)` (KYC via Keyring), epoch running, `!isDepositDuringEpochDisabled`, `!isProgrammableBorrower`, `!isAYSActive`, and a non-empty tranche — all satisfiable by an unprivileged KYC-passing lender in the default fixed-APR epoch mode. No privileged action is needed; the attacker simply calls `depositDuringEpoch`.

### Recommendation
Measure the real inflow inside `depositDuringEpoch`:

```solidity
uint256 _preBal = _contractTokenBalance(token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
uint256 _received = _contractTokenBalance(token) - _preBal;
```

Then compute `interest`, `trancheInterest`, `_minted`, `mintStrategyTokens`, and the borrower transfer from `_received` instead of `_amount` (optionally `require(_received >= _amount * minBps / FULL_ALLOC)` to bound the fee). Apply the same pre/post balance pattern already used in `IdleCDOCreditVault._deposit` (contracts/IdleCDOCreditVault.sol:203-206).

### Proof of Concept
Foundry fork test sketch (fork mainnet, point the vault `token` at a fee-on-transfer ERC20 or deploy a mock fee token as `token` in a forked deployment):

```solidity
function testFeeOnTransferDepositDuringEpoch() public {
    // Assumptions: epoch running, AA tranche seeded, wallet KYC-allowed,
    // vault holds L liquidity (buffer deposits / withdraw funding).
    uint256 amount = 1_000e6;          // e.g. USDC-like 6-dec fee token, 1% fee
    vm.startPrank(attacker);
    feeToken.approve(address(cdo), amount);
    uint256 vaultBalBefore = feeToken.balanceOf(address(cdo));
    uint256 borrowerBefore = feeToken.balanceOf(borrower);

    cdo.depositDuringEpoch(amount, cdo.AATranche());

    uint256 received = feeToken.balanceOf(address(cdo)) + 
        (feeToken.balanceOf(borrower) - borrowerBefore) - vaultBalBefore;
    // vault net change = (amount - fee) - amount = -fee
    assertEq(received, amount - amount * 1 / 100);       // only 990 arrived at vault
    assertEq(feeToken.balanceOf(borrower) - borrowerBefore, amount); // but 1000 sent out

    // attacker shares redeemable for ~amount + prorated interest
    uint256 shares = AA.balanceOf(attacker);
    assertGt(cdo.tranchePrice(address(AA)) * shares / 1e18, amount - amount / 100);
    // i.e., deficit = fee borne by other depositors' NAV
}
```

Run: `forge test --fork-url $RPC --mt testFeeOnTransferDepositDuringEpoch`. Expected: vault token balance decreases by `fee` net, borrower receives full `amount`, and the attacker holds shares priced above the tokens actually contributed — demonstrating both over-mint and drainage of existing vault liquidity.