### Title
Unprivileged lender can force a solvent epoch pool into default and freeze all user funds via oversized instant-withdraw requests - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external report (CVE-2025-70886) is an unauthenticated DoS: a crafted payload to a public endpoint disables the service. The credit-vault analog is a crafted `requestWithdraw`/`requestInstantWithdraw` payload from any KYC-passing tranche holder: when a pending instant-withdraw request exceeds the underlyings available at `startEpoch`, the CDO forwards all cash to the strategy, gives the borrower nothing, and leaves `pendingInstantWithdraws > 0`. Because `stopEpoch` reverts while `_pendingInstant() != 0`, the manager is forced to call `getInstantWithdrawFunds`, whose `transferFrom` on a borrower that never received funds fails, triggering `_handleBorrowerDefault` — a full pool default (`defaulted`, `paused`, withdraw requests disabled) caused entirely by an unprivileged user on a solvent facility.

### Finding Description
In `IdleCDOEpochVariant.startEpoch` (contracts/IdleCDOEpochVariant.sol:279-290), instant-withdraw requests are senior to the borrower funding:

```solidity
uint256 pendingInstant = _pendingInstant();
uint256 totUnderlyings = _contractTokenBalance(token);
_strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);
if (pendingInstant > totUnderlyings) {
  _startEpochProgrammableBorrower(_pendingWithdraws);
  return; // borrower receives nothing
}
```

`collectInstantWithdrawFunds` (contracts/strategies/idle/IdleCreditVault.sol:398-403) reduces `pendingInstantWithdraws` only by the amount actually collected, so the unfunded remainder persists.

At `stopEpoch` (contracts/IdleCDOEpochVariant.sol:338-351) the check `_pendingInstant() != 0` reverts, so the epoch cannot be closed. The only recovery path is `getInstantWithdrawFunds` (lines 558-574), which does `transferFrom(borrower, pendingInstant)`; when the borrower holds no underlyings (it never received epoch funds), the transfer fails and the `catch` arm calls `_handleBorrowerDefault(_instantWithdraws)` (lines 577-598), which sets `defaulted = true`, pauses the contract, stops the epoch, and disables AA/BB withdraw requests.

Any tranche holder can create an instant request via `requestWithdraw` when instant withdrawals are enabled (e.g., after the APR is lowered — see `test/foundry/IdleCreditVault.t.sol:2054-2065`). The request is denominated only by the caller's own tranche balance; there is no cap relative to idle liquidity.

### Impact Explanation
A single KYC'd lender holding tranche tokens slightly larger than the pool's idle cash can permanently block `stopEpoch` and force `defaulted = true` on a borrower that never drew funds. Consequences:

- All LP principal and pending normal-withdraw receipts (`pendingWithdraws`, `withdrawsRequestsByEpoch`) are frozen — no new requests are accepted (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` set false) and `stopEpoch`/`startEpoch` can no longer run.
- Users can only exit through the default-recovery flow (`finalizeDefault`/`finalizeDefaultRecovery`, `defaultRecoveryPrice`), i.e., every claim is haircut if the manager cannot source 100% of `defaultPendingClaimBasis` — even though no borrower loss occurred.
- Attacker cost is limited to the unfunded remainder `ε` of their own instant receipt (the funded portion is eventually recoverable), while the frozen amount scales to the entire pool TVL.

This is temporary freezing of all user funds (and potential haircut losses) caused by an unprivileged transaction — the accepted impact class for this bug class.

### Likelihood Explanation
Prerequisites: the pool operates in instant-withdraw-enabled mode (low APR regime, `disableInstantWithdraw == false`, non-programmable borrower), and idle underlyings at `startEpoch` are smaller than the attacker's instant request — i.e., most TVL is lent out, which is the normal operating state of a credit pool. The attacker only needs to be a KYC-passing tranche holder and to front capital roughly equal to idle cash + ε, most of which is returned at par. No privileged cooperation is required; every step uses the designed code path (`testGetInstantWithdrawFundsDefault` at test/foundry/IdleCreditVault.t.sol:2811-2839 demonstrates the default trigger itself).

### Recommendation
Do not let instant-withdraw demand exceed what the borrower can be forced to cover, or decouple unpayable instant requests from borrower default:

- Cap each instant request (or aggregate `pendingInstantWithdraws`) at the underlyings that will actually be available to fund it (`totUnderlyings` at epoch start, or a manager-set ceiling), so `pendingInstant > totUnderlyings` cannot arise.
- Alternatively, treat an unfunded instant remainder as a normal queued withdraw (convert it into `pendingWithdraws` for the next `stopEpoch`) instead of requiring `getInstantWithdrawFunds` → borrower transfer → default.
- At minimum, allow `stopEpoch` to proceed with `pendingInstantWithdraws > 0` by haircutting unfunded instant receipts via `lossRecoveryPriceByEpoch` rather than forcing a borrower default for cash the borrower never received.

### Proof of Concept
Foundry fork PoC sketch (mirroring `testGetInstantWithdrawFundsDefault` setup):

```solidity
// Pool is running; manager lowers APR so instant withdrawals are enabled.
idleCDO.depositAA(amountWei);          // attacker deposit (>= idle cash + 1 wei)
// ... normal epoch 0 runs, most funds are with the borrower (idle cash small) ...
_stopEpochAndCheckPrices(0, lowApr, _expectedFundsEndEpoch());

// Attacker requests instant withdraw > expected idle cash
uint256 idleCash = underlying.balanceOf(address(cdoEpoch));
cdoEpoch.requestWithdraw(toTranche(idleCash + 1), address(AAtranche));

// startEpoch: all cash -> strategy, borrower gets 0, pendingInstantWithdraws stays > 0
_startEpochAndCheckPrices(1);
assertGt(strategy.pendingInstantWithdraws(), 0);
assertEq(underlying.balanceOf(borrower), 0);   // borrower never funded

vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
vm.prank(manager);
cdoEpoch.getInstantWithdrawFunds();     // transferFrom(borrower, ε) fails

assertTrue(cdoEpoch.defaulted());       // solvent pool forced into default
assertTrue(cdoEpoch.paused());
assertFalse(cdoEpoch.allowAAWithdrawRequest());

// Other users cannot request or be paid; their claims now wait for
// finalizeDefault/recovery and are haircut by defaultRecoveryPrice.
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpoch(0, 0);               // epoch permanently stopped
```

Key lines: `contracts/IdleCDOEpochVariant.sol:279-303` (instant seniority + early return), `:345` (`_pendingInstant() != 0` stopEpoch revert), `:566-573` (borrower pull → default), `:577-598` (`_handleBorrowerDefault`), and `contracts/strategies/idle/IdleCreditVault.sol:398-403` (`collectInstantWithdrawFunds` only partially decrements `pendingInstantWithdraws`).