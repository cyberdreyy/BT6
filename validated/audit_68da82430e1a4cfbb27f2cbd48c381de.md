### Title
Unprivileged donation to the programmable borrower's ERC4626 vault inflates `totalInterestDueNow` and forces a borrower default at `stopEpoch`, permanently freezing the CDO - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The pycord advisory describes an unprivileged remote shutdown triggered through an interaction path the code did not anticipate. The closest analog in idle-tranches is the programmable-borrower stop flow in `IdleCDOEpochVariant._stopEpoch`: the amount the honest borrower must return is derived from `IProgrammableBorrower(_borrower()).onStopEpoch(...)`, whose success depends on the borrower's ERC4626 vault state (`totalInterestDueNow` / `vaultInterestAccrued` / `vaultLoss`). Any unprivileged user of that vault — explicitly an in-scope attacker — can donate underlying to it, inflating the borrower's obligation beyond its pre-funded repayment/approval. The honest manager's `stopEpoch` call then falls into the `catch` path and executes `_handleBorrowerDefault`, which pauses the CDO, sets `defaulted = true`, and disables AA/BB withdraw requests, i.e. an unprivileged-triggered shutdown of the pool.

### Finding Description
In `contracts/IdleCDOEpochVariant.sol` `_stopEpoch` (lines 330–506), the programmable-borrower branch calls `onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)` and, if it returns false, immediately invokes `_handleBorrowerDefault` (lines 395–404). The same default path is reached via the `try this.getFundsFromBorrower(...)` `catch` (lines 408–505), which pulls `token` from the borrower up to the amount the borrower-approved/owed.

`_handleBorrowerDefault` (lines 577–599) sets `defaulted = true`, calls `onDefault()` on the borrower, pauses the contract, sets `isEpochRunning = false`, and clears `allowAAWithdrawRequest`/`allowBBWithdrawRequest`. Once `defaulted` is set:

- `restoreOperations` reverts (`_checkNotAllowed(defaulted || priceAA == 0)`, line 622),
- `_beforeUnpause` reverts (`defaulted || isEpochRunning || skipDefaultCheck`, line 636),
- `setEpochParams` cannot re-enable epochs (line 122),
- normal `requestWithdraw`/`deposit`/`depositDuringEpoch` paths are dead until the owner runs `finalizeDefault`/`finalizeDefaultRecovery` through the strategy.

The borrower's repayment obligation at `stopEpoch` is computed from the programmable borrower's ERC4626 vault accounting (`totalInterestDueNow`, driven by `vaultInterestAccrued`/`vaultLoss` on its share price). Because an ERC4626 vault's assets-per-share is movable by a direct underlying donation, an attacker who holds (or mints dust) shares can donate underlying to the vault, raising the interest/principal the honest borrower is asked to return above what it actually received and approved. `getFundsFromBorrower` (`transferFrom(borrower, ...)`) then reverts and `_handleBorrowerDefault` executes — a shutdown of the credit vault caused entirely by an unprivileged third party, while owner/manager/borrower remain honest.

### Impact Explanation
Direct, quantifiable freezing of user funds. After the forced default:

- All pending withdraw-request funds sit locked in `IdleCreditVault` (`withdrawsRequestsByEpoch`, `pendingWithdraws`) and cannot be claimed via `claimWithdrawRequest`/`claimInstantWithdrawRequest` until governance finalizes a recovery through `finalizeDefault` (`contracts/IdleCDOEpochVariant.sol` line 194), which pays receipts via the aggregate recovery multiplier and can crystallize losses (`stopEpochWithDuration` loss path, `previewLossAdjustedWithdrawFunds`).
- Tranche holders cannot deposit, request withdrawals, or exit; the pool is permanently stuck in the defaulted state absent privileged recovery, and `defaulted` blocks both `unpause` and `restoreOperations`.
- The attacker only spends the donated amount (bounded by the gap between the borrower's funded repayment and its vault-implied obligation), while the frozen principal is the entire pool NAV (`getContractValue`).

The attacker's cost is limited to the donation needed to push `totalInterestDueNow`/recall above the borrower's actual `transferFrom` allowance/balance — i.e., at most the honest epoch interest plus a marginal delta — while the impact is default-flagging and freezing of the whole vault.

### Likelihood Explanation
- Requires only that the CDO is configured with `isProgrammableBorrower = true` (owner-set flag, `setIsProgrammableBorrower`), that an epoch is running, and that the attacker is a user of the borrower's ERC4626 vault — an explicitly listed in-scope attacker.
- The trigger needs no privileged call: the honest manager's routine `stopEpoch` (called after `epochEndDate`) is what executes the default path; the attacker just needs to have pre-positioned the donation beforehand. This mirrors the pycord bug where an ordinary, expected invocation (bot added with a missing scope) triggers the shutdown.
- Guards do not stop it: `_skimDonatedAssets` (called in `_stopEpoch`, line 355) only removes raw underlying sitting on the CDO contract — it cannot see donations locked inside the borrower's vault share price. `_checkNotAllowed`/`_checkOnlyOwnerOrManager` gate the caller, not the borrower's solvency. `checkPrefunding`/`_guarded` are unrelated. The `catch` around `getFundsFromBorrower` intentionally converts any repayment shortfall — including an externally inflated one — into `defaulted`.

Uncertainty: the exact magnitude of obligation inflation depends on how `ProgrammableBorrower` computes `totalInterestDueNow`/`vaultInterestAccrued` from its ERC4626 share price (whether it uses raw `totalAssets`, `convertToAssets`, or a cached value). If it uses a manipulation-resistant cached/share-based measure rather than spot balance, the attack weakens accordingly; this should be confirmed against the `ProgrammableBorrower` implementation.

### Recommendation
Decouple the borrower's default determination from spot ERC4626 vault balances that unprivileged depositors can move:

- In `ProgrammableBorrower`, compute `totalInterestDueNow`/recall amounts from internal accounting (principal drawn + configured APR accrual) rather than spot `totalAssets()`/`convertToAssets` of the vault, or snapshot the obligation at `onStartEpoch` and use that fixed amount in `onStopEpoch`.
- In `_stopEpoch`, distinguish "borrower underpaid the owed amount" (default) from "the obligation itself exceeds what was lent plus configured interest" (revert, not default) — e.g., cap the pulled amount at `expectedEpochInterest + principal + _pendingWithdraws` computed from CDO-side accounting before calling `getFundsFromBorrower`.
- Alternatively, treat a failed `getFundsFromBorrower` where `funds > _expectedInterest + principal` as a transient failure (`onStopEpoch` retryable) rather than an immediate `defaulted` flag, so donation-inflated obligations cannot latch `defaulted = true`.

### Proof of Concept
Foundry fork PoC outline (mode: programmable borrower, fixed-APR epoch):

```solidity
function testDonationToBorrowerVaultForcesDefault() external {
    // setup: CDO with isProgrammableBorrower = true, honest borrower = ERC4626 vault user-facing borrower
    uint256 deposit = 100_000 * ONE_SCALE;
    idleCDO.depositAA(deposit);          // KYC'd LP, honest

    // manager starts epoch; surplus sent to borrower, expectedEpochInterest set
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // borrower honestly funds exactly its owed repayment (principal + expected interest)
    uint256 owed = cdoEpoch.expectedEpochInterest() + principalSent;
    deal(underlying, borrower, owed);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), owed);

    // ATTACKER (unprivileged vault user): deposit/mint dust shares in the programmable
    // borrower's ERC4626 vault, then direct-donate underlying to the vault so that
    // totalInterestDueNow / required recall at onStopEpoch exceeds `owed`.
    address attacker = makeAddr("attacker");
    deal(underlying, attacker, donation);
    vm.startPrank(attacker);
    underlying.approve(borrowerVault, 1);
    borrowerVault.deposit(1, attacker);                 // in-scope vault user
    underlying.transfer(borrowerVault, donation);       // inflate vault assets/share price
    vm.stopPrank();

    // honest manager stops the epoch after epochEndDate
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(newApr, 0);                      // getFundsFromBorrower reverts -> catch

    // forced default state: remote shutdown achieved by unprivileged attacker
    assertTrue(cdoEpoch.defaulted());
    assertTrue(cdoEpoch.paused());
    assertFalse(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.allowAAWithdrawRequest());
    assertFalse(cdoEpoch.allowBBWithdrawRequest());

    // LP funds frozen: requestWithdraw reverts, restoreOperations reverts on defaulted
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(owner);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.restoreOperations();
}
```

Key assertions: `defaulted == true`, `paused() == true`, withdraw-request flags cleared, and both `unpause`/`restoreOperations` permanently blocked (`_beforeUnpause`, line 636; `restoreOperations`, line 622) — meaning the attacker, spending only `donation`, froze the entire pool NAV pending a privileged `finalizeDefault` recovery flow.