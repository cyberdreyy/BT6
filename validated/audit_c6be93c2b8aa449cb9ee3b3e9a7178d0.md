### Title
Fee-on-transfer underlyings break deposit accounting in `depositDuringEpoch` and `IdleCDOEpochQueue.requestDeposit` — ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol), [File: contracts/IdleCDOEpochQueue.sol](contracts/IdleCDOEpochQueue.sol))

### Summary
The codebase correctly measures the received amount with a balance delta in the main deposit path (`IdleCDO._deposit` uses `_contractTokenBalance(_token) - _preBal`, `IdleCDO.sol:250-253`; `IdleCDOCreditVault._deposit`, `IdleCDOCreditVault.sol:203-206`). However, two unprivileged entry points still bookkeep the nominal `_amount` parameter:

1. `IdleCDOEpochVariant.depositDuringEpoch` — pulls `_amount` but mints shares, updates NAV, mints strategy tokens, and forwards `_amount` to the borrower all using the nominal value (`IdleCDOEpochVariant.sol:685`, `724`, `725`, `730`, `732`).
2. `IdleCDOEpochQueue.requestDeposit` — credits `userDepositsEpochs`/`epochPendingDeposits` with the nominal `amount` after `safeTransferFrom` (`IdleCDOEpochQueue.sol:121`, `124`, `126`). The same pattern exists in `requestWithdraw` for tranche tokens (line 135).

If the vault `token` is a fee-on-transfer or deflationary ERC20 (e.g., USDT with transfer fee enabled), the recorded/transferred accounting exceeds the tokens actually received.

### Finding Description
In `depositDuringEpoch`, the flow is:

```solidity
_transferUnderlyingsFrom(msg.sender, address(this), _amount);   // receives _amount - fee
...
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;  // priced on _amount
_mintShares(_tranche, msg.sender, _minted, _amount);            // lastNAV += _amount
IdleCreditVault(strategy).mintStrategyTokens(_amount);          // strategyTokens += _amount
_transferUnderlyings(_borrower(), _amount);                     // sends _amount to borrower
```

The contract receives `_amount - fee` but credits the depositor and the NAV for the full `_amount` and then pushes the full `_amount` to the borrower. The deficit `fee` is drawn from whatever underlying balance the CDO holds (unclaimed fee liquidity, pending instant-withdraw reserves, or pre-skim residual funds). If the balance is insufficient the call reverts — permanently disabling `depositDuringEpoch` for that vault configuration.

In the queue, `requestDeposit` records `amount` while only `amount - fee` arrives. `processDeposits` later calls `_cdo.depositAA(_pending)`/`depositBB(_pending)` (`IdleCDOEpochQueue.sol:239-241`), which pulls the nominal `_pending` from the queue; since the queue holds less than `_pending`, the transfer reverts. `processDepositsToBorrower` (line 179) has the same flaw. Honest depositors must individually call `deleteRequest` to recover funds; the last deleter's `safeTransfer(msg.sender, amount)` (line 198) reverts or the shortfall (the accumulated fee) is socialized onto remaining users, and `epochPendingDeposits` retains the attacker's phantom entry, keeping `processDeposits` reverting for that epoch.

### Impact Explanation
- `depositDuringEpoch`: an unprivileged, wallet-allowed depositor mints tranche shares for `_amount` while delivering `_amount - fee`. NAV (`lastNAVAA`/`lastNAVBB`) and strategy-token supply are inflated by `fee`, breaking the fair-mint/solvency invariant; the missing tokens are taken from contract-held liquidity belonging to other users (e.g., reserved withdraw liquidity), i.e., direct theft up to the fee amount per deposit, repeatable.
- Epoch queue: phantom `epochPendingDeposits` causes `processDeposits`/`processDepositsToBorrower` to revert permanently for the epoch → temporary-to-permanent freezing of all queued deposits for that epoch; last-deleting user absorbs the fee shortfall (quantified loss = accumulated transfer fees).

### Likelihood Explanation
Exploitation requires the vault underlying to charge a transfer fee. The contracts are explicitly designed to accept an arbitrary `token` set at initialization (`underlying = _cdo.token()` in the queue/escrow), and the developers already added balance-delta accounting in `_deposit`, showing fee-on-transfer was a design concern that was applied inconsistently. USDT — a canonical credit-vault underlying — supports an optional transfer fee. Attacker cost is only the deposit itself; no privileged role is involved. Likelihood is moderate precisely because it hinges on token selection, which justifies Medium severity consistent with the referenced report.

### Recommendation
Measure received amounts via balance deltas in every entry point:

- In `depositDuringEpoch`: snapshot `IERC20Detailed(token).balanceOf(address(this))` before `_transferUnderlyingsFrom`, compute `received = balanceAfter - balanceBefore`, and use `received` for `_calcInterest`, share minting, `_mintShares`, `mintStrategyTokens`, and the borrower transfer.
- In `IdleCDOEpochQueue.requestDeposit`/`requestWithdraw`: credit `userDepositsEpochs`/`userWithdrawalsEpochs` and the epoch counters with `balanceAfter - balanceBefore` instead of `amount`.
- Optionally, reject known fee-on-transfer tokens at initialization, or document that only non-fee stables are supported.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCDOEpochQueue} from "../contracts/IdleCDOEpochQueue.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

/// @dev fee-on-transfer ERC20: 1% burned on every transfer/transferFrom
contract FeeToken is IERC20Detailed {
    mapping(address => uint256) public balanceOf_;
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        uint256 fee = a / 100;
        balanceOf_[f] -= a; balanceOf_[t] += a - fee; // fee burned
        return true;
    }
    // ... other ERC20 fns
}

contract FeeOnTransferPoC is Test {
    function test_DepositDuringEpoch_Overmints() public {
        // setup: CDO epoch running, borrower funded, attacker KYC-allowed
        FeeToken tok; IdleCDOEpochVariant cdo;
        address attacker = makeAddr("attacker");

        uint256 amount = 1_000_000e6;
        deal(address(tok), attacker, amount);

        vm.startPrank(attacker);
        tok.approve(address(cdo), amount);
        uint256 minted = cdo.depositDuringEpoch(amount, cdo.AATranche());
        vm.stopPrank();

        // minted shares are priced on `amount`, but CDO only received `amount * 99/100`
        // attacker can requestWithdraw and redeem ~amount worth of underlying,
        // the ~1% shortfall was taken from CDO-held liquidity of other users
    }

    function test_Queue_ProcessDepositsReverts() public {
        FeeToken tok; IdleCDOEpochQueue queue;
        address honest = makeAddr("honest");
        address attacker = makeAddr("attacker");

        vm.prank(honest);   queue.requestDeposit(100e6);   // queue receives 99e6
        vm.prank(attacker); queue.requestDeposit(100e6);   // queue receives 99e6

        // epoch rolls; owner tries to process pending = 200e6 but queue holds 198e6
        vm.expectRevert(); // ERC20: transfer amount exceeds balance
        queue.processDeposits();

        // honest user deletes: gets back full 100e6, leaving 98e6 for attacker entry
        vm.prank(honest); queue.deleteRequest(epoch);
        // attacker's phantom 100e6 entry remains -> processDeposits reverts forever,
        // and deleting it would need 100e6 while only 98e6 remain
    }
}
```

Note on residual uncertainty: the magnitude of the `depositDuringEpoch` theft depends on whether the CDO holds a positive underlying balance during a running epoch (e.g., withdraw reserves) to cover the fee shortfall; if its balance is exactly zero, the call reverts and the impact degrades to a DoS of that function. The queue path is unconditional in its accounting flaw — it reverts regardless of other balances — making it the strongest demonstrated analog.