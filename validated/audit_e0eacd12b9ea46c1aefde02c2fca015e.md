### Title
`depositDuringEpoch` credits and forwards the full requested `_amount` instead of the actual balance received, enabling theft of vault liquidity with fee-on-transfer / deflationary underlyings - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The normal deposit path (`_deposit` in `IdleCDO`/`IdleCDOCreditVault`) correctly measures the actual balance delta after `transferFrom` and mints shares only for tokens actually received. The mid-epoch deposit path `depositDuringEpoch` in `IdleCDOEpochVariant` skips that measurement entirely: it mints tranche shares priced on the full requested `_amount`, accrues `expectedEpochInterest` on `_amount`, mints `_amount` strategy tokens via `IdleCreditVault.mintStrategyTokens`, and then transfers the full `_amount` to the borrower — even if the vault actually received less due to a transfer fee or deflationary token mechanics. The shortfall is pulled from underlying already sitting in the CDO contract that belongs to other users (buffer-phase deposits, pending withdrawal liquidity), and the attacker is credited NAV and yield on funds never delivered.

### Finding Description
In `depositDuringEpoch` (IdleCDOEpochVariant.sol:656-733):

```solidity
// contracts/IdleCDOEpochVariant.sol:685
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
...
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
_mintShares(_tranche, msg.sender, _minted, _amount);       // NAV credited with full _amount
expectedEpochInterest += interest;                          // interest computed on _amount
IdleCreditVault(strategy).mintStrategyTokens(_amount);      // strategy tokens minted 1:1 for _amount
_transferUnderlyings(_borrower(), _amount);                 // full _amount pushed to borrower
```

Compare with the safe parent path (contracts/IdleCDO.sol:250-253 and contracts/IdleCDOCreditVault.sol:203-206):

```solidity
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

`depositDuringEpoch` never computes `_contractTokenBalance(_token) - _preBal`. If `token` takes a fee on transfer (e.g., a deflationary token, or an upgradeable stablecoin such as USDT that later enables a fee), the contract receives `_amount * (1 - fee)` but:

- `lastNAVAA`/`lastNAVBB` are incremented by the full `_amount` inside `_mintShares`, inflating NAV and share price basis.
- `mintStrategyTokens(_amount)` mints strategy tokens 1:1 to the CDO with no corresponding underlyings delivered to the strategy (`IdleCreditVault.sol:621-624` mints unconditionally, `_onlyIdleCDO` only).
- `expectedEpochInterest` grows by interest computed on `_amount`.
- `_transferUnderlyings(_borrower(), _amount)` sends the gross `_amount` to the borrower, sourcing the missing fee portion from any other underlying held by the CDO — e.g., deposits queued during the buffer period awaiting `startEpoch`, or liquidity earmarked for pending withdrawals — since the skim only sweeps raw *donations* to `feeReceiver`, not legitimate user funds.

The only guard in the path is `_skimDonatedAssets()` (line 679), `_guarded`, wallet allowlisting (`isWalletAllowed`), and flag checks (lines 658-669). None verify the received amount. A KYC-passed lender (`isWalletAllowed(msg.sender)` satisfied) is a permitted attacker per scope rules.

Attack flow (epoch running, fixed-APR mode, `isDepositDuringEpochDisabled == false`):

1. Owner/manager honestly started an epoch; the CDO holds buffer-period deposits or pending-withdrawal liquidity of honest users.
2. Attacker (KYC'd lender) calls `depositDuringEpoch(X, AA)` with a 1% fee-on-transfer token. Vault receives `0.99X`.
3. Attacker is minted tranche shares priced on `X + trancheInterest`, `expectedEpochInterest` grows, strategy mints `X` tokens to the CDO, and the full `X` is sent to the borrower — `0.01X` of it taken from other users' liquidity.
4. Attacker requests withdrawal next epoch (or holds shares whose NAV now exceeds their net contribution), redeeming more than delivered; the residual `0.01X` per deposit is a direct loss to remaining depositors and breaks the solvency invariant that minted NAV is backed 1:1 by delivered underlyings.

### Impact Explanation
Direct theft / insolvency: each call mints shares and strategy tokens backed by less underlying than accounted, and forwards the deficit from funds belonging to other depositors to the borrower. The attacker effectively deposits `fee-discounted` tokens while being credited full principal plus interest. Loss magnitude equals the transfer fee on every deposit; with a ~1% fee token an attacker can repeatedly extract up to 1% of each deposit from the contract's residual liquidity, or in a thin pool cause withdrawals to be undercollateralized at epoch stop. If the contract holds no spare balance the borrower transfer reverts, but wherever legitimate liquidity exists it is drained to back the attacker's inflated shares.

### Likelihood Explanation
Requires (a) the vault's `token` to charge a transfer fee or deflate, and (b) `depositDuringEpoch` enabled (`isDepositDuringEpochDisabled == false`), epoch running, and a non-empty tranche supply. Condition (a) is the same consideration the Sherlock judges accepted as medium: curated tokens can be upgradeable (USDT, USDC class) and may later enable fees, and PAXG-type tokens already charge on-transfer fees. Condition (b) is a normal operating mode explicitly supported by the code and tested in `test/foundry/IdleCreditVault.t.sol` (`testDepositDuringEpochNumericalAA`). No privileged cooperation is needed; the attacker only needs to pass the same Keyring wallet check as any lender.

### Recommendation
Mirror the `_deposit` pattern inside `depositDuringEpoch`: measure the actual received amount and use it for all downstream accounting.

```solidity
// contracts/IdleCDOEpochVariant.sol depositDuringEpoch
uint256 _preBal = _contractTokenBalance(token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
uint256 _received = _contractTokenBalance(token) - _preBal;
```

Then compute `interest`, `_minted`, `expectedEpochInterest`, `mintStrategyTokens`, and the borrower transfer from `_received` (or revert if `_received != _amount` to disallow fee tokens explicitly, matching the `_checkNotAllowed` style used elsewhere in the function).

### Proof of Concept
Foundry fork test sketch (run against a mainnet fork; deploy the vault with a fee-on-transfer underlying — e.g., a minimal `FeeToken` that burns 1% on transfer, or PAXG which has a native ~0.02% transfer fee):

```solidity
// test/foundry/DepositDuringEpochFeeToken.t.sol
function testDepositDuringEpochFeeOnTransfer() public {
    // setup: fork mainnet, deploy FeeOnTransferERC20 (1% burn on transfer) as `token`,
    // deploy IdleCreditVault + IdleCDOEpochVariant, whitelist attacker via Keyring mock,
    // owner deposits 1000e18 AA honestly, startEpoch() called by manager.
    // warp into the running epoch; setIsDepositDuringEpochDisabled(false).

    uint256 cdoBalBefore = feeToken.balanceOf(address(cdoEpoch));
    uint256 borrowerBalBefore = feeToken.balanceOf(borrower);
    uint256 navAAbefore = cdoEpoch.lastNAVAA();

    deal(address(feeToken), attacker, 1000e18);
    vm.startPrank(attacker);
    feeToken.approve(address(cdoEpoch), 1000e18);
    uint256 minted = cdoEpoch.depositDuringEpoch(1000e18, address(AAtranche));
    vm.stopPrank();

    // vault only received 990e18 (1% fee)...
    assertEq(feeToken.balanceOf(address(cdoEpoch)) - cdoBalBefore + (feeToken.balanceOf(borrower) - borrowerBalBefore), 990e18);
    // ...but NAV, strategy tokens and expected interest were credited for 1000e18
    assertEq(cdoEpoch.lastNAVAA() - navAAbefore, 1000e18);            // NAV inflated by the 10e18 fee
    assertEq(IERC20(address(strategy)).balanceOf(address(cdoEpoch)) >= 1000e18, true);
    // attacker holds shares worth ~1000e18+interest having delivered only 990e18
    // the missing 10e18 was sourced from honest users' liquidity in the contract
    // => solvency break: sum of tranche NAVs > underlying attributable to the vault
}
```

Uncertainty note: the exact per-test assertion values depend on the configured epoch APR/buffer (the `trancheInterest` term), and whether `isDepositDuringEpochDisabled` defaults to enabled for the target deployment — both are settable via the existing manager/owner setters exercised in `IdleCreditVault.t.sol`. The core defect — gross `_amount` used for minting, NAV, strategy-token minting and the borrower transfer with no received-balance check — is confirmed directly at `contracts/IdleCDOEpochVariant.sol:685-732` versus the balance-delta pattern at `contracts/IdleCDO.sol:250-253`.