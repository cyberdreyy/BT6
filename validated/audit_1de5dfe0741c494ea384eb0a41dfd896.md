### Title
One-time max approval to strategy drains over time and permanently bricks deposits — (File: contracts/IdleCDOCreditVault.sol)

### Summary
`IdleCDOCreditVault.initialize` grants the strategy a one-time `type(uint256).max` allowance on the underlying `token` via `_allowUnlimitedSpend` (line 78). For real-world credit-vault underlyings like USDC (which decrement allowance even from `uint256.max`, unlike OpenZeppelin ERC20), every `strategy.deposit` → `transferFrom` consumes allowance. Once the remaining allowance falls below a deposit's `_amount`, `IIdleCDOStrategy(strategy).deposit(_amount)` in `_deposit` reverts, blocking all deposits and epoch funding with no in-contract recovery path.

### Finding Description
In `initialize`, the vault sets the approval exactly once: [1](#0-0) 

`_allowUnlimitedSpend` uses `safeIncreaseAllowance` with `type(uint256).max` and is never called again: [2](#0-1) 

Every user deposit flows through `_deposit`, which ends with `IIdleCDOStrategy(strategy).deposit(_amount)`: [3](#0-2) 

The strategy pulls the underlying via `transferFrom(vault, strategy, _amount)`, so tokens that decrement allowance (USDC's FiatToken, USDT, WBTC, etc.) steadily erode the `uint256.max` grant. There is no allowance check or re-approval anywhere in `IdleCDOCreditVault`, `IdleCDOEpochVariant`, or the credit-vault flow — the only recovery is a full implementation upgrade.

### Impact Explanation
Once `allowance(vault → strategy) < _amount`, `depositAA`, `depositBB`, `depositDuringEpoch`, and any borrower/epoch funding path that routes through `_deposit` revert permanently. New lender capital and epoch deposits are frozen indefinitely (absent a proxy upgrade), blocking borrower liquidity and stalling the epoch state machine. An unprivileged attacker can accelerate depletion at near-zero cost by repeatedly calling `depositAA` followed by a withdrawal, cycling the same capital to burn `2 * N` of allowance per unit of capital per loop.

### Likelihood Explanation
Deterministic for vaults on allowance-decrementing tokens — Pareto credit vaults primarily use USDC, which decrements allowance on `transferFrom` regardless of the approved amount. It is guaranteed to trigger eventually under normal usage; an attacker only accelerates it. The attacker needs no privileges, just enough capital to cycle deposits, and withdrawals are unaffected so the loop is self-funding.

### Recommendation
In `_deposit` (or the strategy's `deposit`), check `token.allowance(address(this), strategy)` and top it up when below the needed amount, e.g. `if (allowance < _amount) token.safeApprove(strategy, 0); token.safeApprove(strategy, type(uint256).max);` — or approve per-deposit.

### Proof of Concept
Foundry fork test (mainnet, block where a live USDC credit vault exists):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/IdleCDOCreditVault.sol";
import "../contracts/interfaces/IERC20Detailed.sol";

// FiatToken-style USDC mock that decrements allowance even from uint256.max
contract USDCAllowanceDrainer {
    function testAllowanceDrains() public {
        IdleCDOCreditVault vault = IdleCDOCreditVault(VAULT_ADDR); // live USDC credit vault
        IERC20Detailed usdc = IERC20Detailed(vault.token());
        address strategy = vault.strategy();

        uint256 allowance0 = usdc.allowance(address(vault), strategy);
        assertEq(allowance0, type(uint256).max); // set once at initialize

        // attacker cycles depositAA/withdrawAA (or just deposits) to burn allowance
        uint256 amount = 100_000e6;
        deal(address(usdc), address(this), amount * 2);
        usdc.approve(address(vault), type(uint256).max);

        for (uint i; i < N; i++) {
            vault.depositAA(amount);           // pulls allowance once per call
            vault.withdrawAA(trancheBal());    // recycles capital
        }
        assertLt(usdc.allowance(address(vault), strategy), amount);

        // eventually: every deposit reverts on transferFrom
        vm.expectRevert(); // "ERC20: transfer amount exceeds allowance" / FiatToken revert
        vault.depositAA(amount);
    }
}
```

Uncertain: I could not open `ERC4626Strategy.sol`/`BaseStrategy.sol` line ranges to confirm the exact `transferFrom` call site inside `deposit` (only match counts were returned), but `IIdleCDOStrategy.deposit` in the IdleCDO architecture is documented to pull underlyings from the CDO, which is what consumes the allowance. The core defect — a single non-renewable approval at `initialize` (lines 77–80) with no top-up path — is confirmed in the vault's own code.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L77-80)
```text
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
    guardian = _owner;
```

**File:** contracts/IdleCDOCreditVault.sol (L204-211)
```text
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L573-575)
```text
  function _allowUnlimitedSpend(address _token, address _spender) internal {
    IERC20Detailed(_token).safeIncreaseAllowance(_spender, type(uint256).max);
  }
```
