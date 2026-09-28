### Title
`depositDuringEpoch` mints tranche shares and strategy tokens for the requested amount instead of the amount received — fee-on-transfer underlyings inflate NAV and over-fund the borrower - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
Unlike `_deposit`, which mints shares from the actual balance delta (`_contractTokenBalance(_token) - _preBal`), `depositDuringEpoch` pulls `_amount` via `_transferUnderlyingsFrom` but then mints `(_amount + trancheInterest)` worth of tranche tokens, mints `_amount` of strategy tokens into NAV, and forwards `_amount` to the borrower — all denominated in the requested amount. With a fee-on-transfer underlying (or any token that delivers less than `_amount`), the vault receives less than it accounts for: NAV and tranche supply are inflated, and the borrower is sent more than the vault actually received. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
During a running epoch (when `isDepositDuringEpochDisabled == false`, `isProgrammableBorrower == false`, `isAYSActive == false`), a KYC-passing lender calls `depositDuringEpoch(_amount, tranche)`. The function:

1. Transfers `_amount` from the depositor — with a fee-on-transfer token the contract receives only `_amount - fee` (`_transferUnderlyingsFrom` at line 685).
2. Mints `(_amount + trancheInterest) * _trancheTotSupply / expectedFinal` tranche tokens, i.e., credits principal plus prorated interest for the full `_amount` (line 724).
3. Increases `expectedEpochInterest` by the interest computed on `_amount` (line 728), so `stopEpoch` will later demand that interest from the borrower.
4. Mints `_amount` strategy tokens (line 730), inflating `getContractValue()` by the full requested amount rather than the received amount.
5. Transfers `_amount` to the borrower (line 732) — more than the vault received, so the shortfall is paid out of other underlyings held in the contract (instant-withdraw buffer funds or residual deposits).

The result: the attacker holds tranche tokens priced at `amount + interest` while only `amount - fee` entered the system; total strategy-token NAV exceeds real backing; and `pendingInstant`/buffer cash meant for withdraw claims is drained to cover the borrower's transfer.

### Impact Explanation
- Direct theft: the extra `_amount - received` sent to the borrower is backed by vault funds earmarked for instant-withdraw claimants and other depositors. If the borrower is honest it remains owed, but the minted shares and inflated `expectedEpochInterest` still let the attacker and price inflation dilute other LPs.
- Insolvency: `getContractValue()` counts `_amount` of strategy tokens while only `received` worth of real assets (borrower claim) exists; tranche prices are inflated and later redeemers/withdraw-request claimants face a shortfall — the classic `poolAmount` over-credit from the Allo report, here splitting the theft between over-minted shares and an over-funded borrower.
- Quantified: loss equals the token fee on every `depositDuringEpoch` call, plus the full `_amount` claim minted to the attacker redeemable at epoch end, while only `amount - fee` of real backing was added.

### Likelihood Explanation
Requires the vault's underlying `token` to charge a transfer fee (or otherwise deliver less than the requested amount). The codebase mitigates this exact bug in `_deposit` (both `IdleCDO` and `IdleCDOCreditVault`) by minting on balance delta, and README/deployments target stablecoin underlyings; however nothing prevents the vault from being deployed against a fee-on-transfer or rebasing-down token, and `depositDuringEpoch`, `mintStrategyTokens`, and the borrower forwarding path all trust `_amount`. Any unprivileged KYC'd lender can trigger it during a running epoch with deposits-during-epoch enabled. Likelihood is moderate-low (conditional on token choice) but the same accounting asymmetry also applies to deflationary-transfer edge cases.

### Recommendation
Mirror the `_deposit` fix: record `_received = _contractTokenBalance(_token) - preBal` after `_transferUnderlyingsFrom`, then compute `interest`, `minted`, `mintStrategyTokens`, `expectedEpochInterest`, and the borrower transfer all on `_received` instead of `_amount`. Alternatively, explicitly document and enforce that `token` must not be fee-on-transfer.

### Proof of Concept
```solidity
// test/foundry/FeeOnTransferDepositDuringEpoch.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract FoTToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    uint256 constant FEE_BPS = 100; // 1%
    function mint(address to, uint256 amt) external { balanceOf[to] += amt; }
    function approve(address s, uint256 a) external { allowance[msg.sender][s] = a; }
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        allowance[f][msg.sender] -= a; balanceOf[f] -= a;
        balanceOf[t] += a * (10000 - FEE_BPS) / 10000; // 1% burned
        return true;
    }
    function transfer(address t, uint256 a) external returns (bool) {
        balanceOf[msg.sender] -= a; balanceOf[t] += a * (10000 - FEE_BPS) / 10000;
        return true;
    }
}

contract FoTDepositDuringEpochTest is Test {
    // Deploy IdleCDOEpochVariant + IdleCreditVault with FoTToken as `token`.
    // Seed: honest KYC lender deposits in buffer so AA supply > 0 and borrower is set.
    // Owner/manager calls startEpoch() -> isEpochRunning = true.
    // Owner calls setIsDepositDuringEpochDisabled(false).

    function test_FoTDepositDuringEpoch_OverMintsAndOverPays() public {
        uint256 amount = 1_000_000e6;
        address attacker = kycLender; // Keyring-passing EOA
        fot.mint(attacker, amount);
        vm.startPrank(attacker);
        fot.approve(address(cdo), amount);
        uint256 preBorrower = fot.balanceOf(borrower);
        uint256 minted = cdo.depositDuringEpoch(amount, cdo.AATranche());
        vm.stopPrank();

        // Vault only received amount * 99% but:
        assertEq(fot.balanceOf(borrower) - preBorrower, amount);        // borrower got full amount
        // minted shares are worth amount + prorated interest at current price
        uint256 attackerValue = minted * cdo.virtualPrice(cdo.AATranche()) / 1e18;
        assertGt(attackerValue, amount * 99 / 100);                      // attacker credited full principal + interest
        // NAV inflated: strategyTokens minted == amount while real backing == 99% of amount
        assertEq(strategyToken.balanceOf(address(cdo)), navBefore + amount);
        // The 1% shortfall was paid out of the instant-withdraw buffer held by the CDO,
        // reducing funds available for claimInstantWithdrawRequest users.
    }
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L684-686)
```text
    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);

```

**File:** contracts/IdleCDOEpochVariant.sol (L719-732)
```text
    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L201-206)
```text
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```
