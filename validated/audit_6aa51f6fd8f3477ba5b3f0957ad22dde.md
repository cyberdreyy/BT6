### Title
Borrower repayments and epoch-end pulls credit the gross `assets`/`_amount` while a fee-on-transfer underlying delivers less, silently forgiving borrower debt and leaving withdrawal claims underfunded - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external finding shows a redemption path that reverts because the code assumes `received == requested` for the underlying token. The same broken assumption exists in `idle-tranches` in the two places where underlying is pulled from the borrower: `ProgrammableBorrower._repay` (contracts/strategies/idle/ProgrammableBorrower.sol:482) and `IdleCDOEpochVariant.getFundsFromBorrower` (contracts/IdleCDOEpochVariant.sol:550-553). Neither measures the actual balance delta after `safeTransferFrom`; both bookkeep the full requested amount. With a fee-on-transfer underlying, the contract receives `amount - fee` but clears `amount` of borrower debt / books `amount` of epoch inflows, producing a permanent shortfall borne by tranche holders.

### Finding Description
In `_repay` (contracts/strategies/idle/ProgrammableBorrower.sol:465-540):

```solidity
underlyingToken.safeTransferFrom(borrower, address(this), assets);
uint256 totalRepaidAssets = assets;
```

`assets` is then used to zero out `borrowerInterestDebt`, `borrowerInterestAccrued`, and `borrowerPrincipal` — the three buckets that define the borrower's obligation (`borrowerPrincipal + borrowerInterestDebt + borrowerInterestAccrued` at line 470). If the token charges even a 1 wei transfer fee, the contract receives less than `assets` while marking the debt fully cleared. `totalInterestDueNow`/`vaultInterestAccrued` accounting is computed from these buckets, so subsequent `stopEpoch` calls understate what the borrower still owes.

The same pattern exists in `getFundsFromBorrower` (contracts/IdleCDOEpochVariant.sol:550-553), which pulls `_amountToPullFromBorrower + _pendingWithdraws` from the borrower via `_transferUnderlyingsFrom` and then calls `_strategy.collectWithdrawFunds(_pendingWithdraws)` and `_strategy.deposit(netInterest)` (lines 408-466) as if the full amount arrived. With a fee-on-transfer token the shortfall is silently absorbed by the CDO's own balance, so `pendingWithdraws` claims and interest accounting are denominated against tokens that never arrived.

Unlike the Notional case there is no `require(received >= expected)` to revert — the flow succeeds and records phantom inflows. `stopEpoch` does not reconcile `balanceOf` before/after the pull, and the `try/catch` only handles outright transfer failure, not partial delivery. No skim or reserve check corrects this: `_skimDonatedAssets` only sweeps *excess* balance above accounting, it never detects a deficit.

### Impact Explanation
Insolvency with quantified loss. Every honest borrower repayment and every `stopEpoch` fund pull leaks `amount * feePercent` of real value while the protocol's books show full payment. Over successive epochs `borrowerPrincipal`/`borrowerInterestDebt` can reach zero while the vault is materially underfunded; tranche holders' withdrawal claims (`claimWithdrawRequest`, `collectWithdrawFunds` payouts) then exceed available underlying. The shortfall is realized by the last claimants or by AA tranches after the BB buffer is exhausted — i.e., a direct loss of LP funds, not merely a revert. If the CDO's own unlent balance cannot cover the gap, `stopEpoch`'s internal transfers revert and the catch block routes to `_handleBorrowerDefault`, forcing a spurious default and socializing the fee loss onto LPs through `defaultRecovery` pricing even though the borrower paid in full.

### Likelihood Explanation
Conditional on the deployed underlying being a fee-on-transfer (or deflationary) token — no attacker action is required; the loss accrues on every honest `repay`/`stopEpoch`. The codebase places no restriction on the underlying token: `IERC20Detailed(token)` is configured at init and `SafeERC20` is used throughout, and the repo's own Morpho interface comments acknowledge FoT tokens exist but are "not supported" by that integration — yet nothing in `ProgrammableBorrower` or `IdleCDOEpochVariant` enforces the same exclusion for the vault's own underlying. Deployments are permissioned so likelihood is medium-low in practice, but the failure is deterministic and cumulative whenever such a token is used.

### Recommendation
Measure actual received amounts rather than assuming gross delivery:

- In `ProgrammableBorrower._repay`, snapshot `underlyingToken.balanceOf(address(this))` before `safeTransferFrom` and set `totalRepaidAssets = balanceAfter - balanceBefore`; apply debt reduction only against the received amount.
- In `IdleCDOEpochVariant.getFundsFromBorrower`, similarly measure the delta and either revert when `received < _amount` (forcing explicit borrower default) or propagate the shortfall to `_stopEpoch` so `collectWithdrawFunds`/`deposit` are called with real amounts.
- Alternatively, explicitly document and enforce a no-fee-on-transfer underlying constraint at deployment (e.g., a one-time self-transfer check in `_additionalInit`), matching the Morpho interface's stated assumption.

### Proof of Concept
Foundry fork/unit PoC (conceptual — the repo's `test/foundry/ProgrammableBorrowerCreditVault.t.sol` harness provides the scaffolding):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ProgrammableBorrower} from "contracts/strategies/idle/ProgrammableBorrower.sol";

// Mock underlying that burns 1% on every transfer
contract FoTToken is Test {
    // ... standard ERC20 with transfer/transferFrom deducting 1% to address(0)
}

contract FeeOnTransferRepayPoC is Test {
    function testRepayCreditsGrossAmount() public {
        // 1. Deploy credit vault + ProgrammableBorrower with FoTToken as `underlying`
        // 2. LP deposits 10_000e6 via idleCDO.depositAA; startEpoch
        // 3. borrower calls programmableBorrower.borrow(4_000e6)
        //    -> borrower receives 3_960e6 (1% fee on the outbound transfer)
        // 4. warp so borrowerInterestAccrued accrues; top up borrower with tokens
        // 5. borrower calls repay(totalOwed) where totalOwed = principal + accrued
        //    -> safeTransferFrom pulls totalOwed but contract receives totalOwed * 0.99
        //    -> borrowerPrincipal / borrowerInterestDebt / borrowerInterestAccrued == 0
        assertEq(programmableBorrower.borrowerPrincipal(), 0);
        // 6. The 1% gap is unbacked: vault/contract holdings < recorded debt repayment
        //    -> subsequent stopEpoch credits lastEpochInterest as if fully funded,
        //       and claimWithdrawRequest payouts exceed real underlying balance.
        // Invariant broken: sum(LP claims) > underlying held => insolvency.
    }
}
```

The same deficit can be shown by calling `getFundsFromBorrower`/`stopEpoch` directly: after `stopEpoch(0, 0)` the strategy's actual underlying balance is `expectedInterest + pendingWithdraws` minus the transfer fee, while `pendingWithdraws` and NAV are updated assuming full delivery, leaving the last withdrawers unpayable.

Caveat: severity depends entirely on whether the deployed `underlying` charges transfer fees; for plain USDC the bug is latent. The finding holds on idle-tranches' own code paths (`_repay`, `getFundsFromBorrower`) rather than relying on the Notional report's specifics.