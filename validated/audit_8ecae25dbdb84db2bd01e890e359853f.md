### Title
`depositDuringEpoch` force-lends attacker deposits to the borrower mid-epoch, inflating `expectedEpochInterest` and enabling forced-default griefing - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`IdleCDOEpochVariant.depositDuringEpoch` lets any KYC-passing wallet deposit underlying while an epoch is running. The function immediately transfers the full deposit to the borrower and adds prorated interest to `expectedEpochInterest`, without any borrower opt-in. This mirrors the external `AccountableFixedTerm::pay` auto-draw bug: an unprivileged lender deposit unilaterally increases the borrower's outstanding obligation (principal + interest due at `stopEpoch`), and can push the borrower over its approved/available repayment capacity so that `getFundsFromBorrower` reverts and `_handleBorrowerDefault` permanently defaults the pool.

### Finding Description
In `depositDuringEpoch`, after minting tranche tokens to the depositor the contract does two things that touch the borrower's debt surface:

- `expectedEpochInterest += interest;` (line 728) — raises the amount the borrower must return at `stopEpoch`, computed on the attacker's `_amount` for the remaining epoch plus the full buffer (lines 691-697).
- `_transferUnderlyings(_borrower(), _amount);` (line 732) — pushes the principal to the borrower address unconditionally.

There is no borrower approval hook, no draw request, and no cap tied to borrower consent — the only gates are `isWalletAllowed(msg.sender)` (Keyring KYC, satisfiable by the attacker per scope rules), the `isDepositDuringEpochDisabled` flag (a supported deployment mode, not a guard), `!isEpochRunning`, `block.timestamp < epochEndDate`, and `_guarded` (a TVL cap, not a borrower-debt cap). Unlike `ProgrammableBorrower.borrow`, which requires `msg.sender == borrower`, the fixed-term borrower path has no discretion: any allowed lender decides to enlarge the loan at any point in the epoch.

At `stopEpoch`, the CDO executes `this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws)` which performs `transferFrom(_borrower, this, _amount)` (lines 550-553, 408). `getInstantWithdrawFunds` does the same mid-epoch (line 566). If the forced draws push the obligation beyond what the honest borrower has approved or can source, the transfer reverts and the catch block calls `_handleBorrowerDefault` (line 504): `defaulted = true`, epoch stopped, deposits and withdraw requests disabled, and the pool is locked until `finalizeDefault` crystallizes losses on all tranche holders.

### Impact Explanation
- **Forced debt increase / griefing:** each attacker deposit increases the borrower's end-of-epoch liability by `_amount` principal plus `interest = _calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)` — the borrower never agreed to borrow these funds and cannot refuse the inbound transfer (an EOA/borrower wallet cannot revert a plain ERC20 `transfer`).
- **Forced default:** if the borrower sized its `approve` and liquidity for the obligation it expected, stuffing the pool with deposits right before `epochEndDate` makes `transferFrom` fail at `stopEpoch`/`getInstantWithdrawFunds`, converting an honest borrower into a hard default: `defaulted = true`, LPs frozen, and losses socialized via `finalizeDefault`/`DefaultDistributor`. The attacker is a KYC'd lender, so `isWalletAllowed` does not stop this; `_skimDonatedAssets` only removes raw donations, not deposited funds; `_guarded` limits TVL, not borrower willingness.
- The attacker retains tranche tokens and can later request withdrawal, so the deposit is recoverable — the attack costs only gas plus one epoch of opportunity cost, while the borrower (and pool) bears default handling.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled == false` (an explicitly supported mode; the flag exists precisely to toggle this feature) and a non-programmable, non-AYS deployment — the default fixed-APR epoch configuration. The attacker only needs Keyring credentials, which the threat model grants to ordinary lenders. The malicious deposit can be placed any time before `epochEndDate`, including the last block, leaving the borrower no reaction window. Repeated "stuffing" each epoch compounds the griefing: every buffer/running cycle the borrower must source repayment for debt it never requested.

### Recommendation
Do not forward mid-epoch deposits to the borrower automatically. Either:

- Route `depositDuringEpoch` proceeds to `IdleCreditVault` (strategy) instead of `_borrower()`, and let the borrower pull them only via an explicit borrower-initiated draw, or
- Add a borrower consent/acknowledgement requirement (e.g., a per-epoch borrow ceiling set by the borrower, or require `msg.sender == borrower` on the forwarding leg) before any deposit increases `expectedEpochInterest` and is transferred out.

Equivalently, at `stopEpoch`/`getInstantWithdrawFunds`, distinguish "borrower cannot repay consented debt" from "unconsented draw exceeded approval" — though the cleaner fix is removing the implicit draw entirely, matching the upstream fix (`03f871b`) that removed auto-draw from `pay`.

### Proof of Concept
Foundry fork test sketch (pool with `isDepositDuringEpochDisabled = false`, fixed-APR, non-programmable borrower):

```solidity
function testForcedMidEpochDrawCausesDefault() external {
    // Setup: epoch running, borrower has approved only the consented obligation
    // borrower approved exactly expectedEpochInterest + pendingWithdraws for this epoch

    uint256 borrowerExpectedDebt = cdoEpoch.expectedEpochInterest()
        + IdleCreditVault(strategy).pendingWithdraws();
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), borrowerExpectedDebt); // sized for consented debt

    // Attacker: a Keyring-allowed lender deposits mid-epoch
    uint256 attackAmt = 1_000_000 * 1e6;
    deal(address(underlying), attacker, attackAmt);
    keyring.whitelist(attacker); // KYC-passing lender

    vm.warp(epochEndDate - 1 hours);
    vm.prank(attacker);
    cdoEpoch.depositDuringEpoch(attackAmt, address(aaTranche));

    // 1) Borrower received funds it never asked for
    assertEq(underlying.balanceOf(borrower) - balPre, attackAmt);
    // 2) expectedEpochInterest grew by prorated interest on attackAmt
    assertGt(cdoEpoch.expectedEpochInterest(), interestPre);

    // 3) At stopEpoch, transferFrom pulls interest on the forced draw too;
    //    borrower approval/liquidity was sized without it -> revert -> default
    vm.warp(epochEndDate + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(apr, 0);

    assertTrue(cdoEpoch.defaulted());      // honest borrower forced into default
    assertFalse(cdoEpoch.allowAAWithdrawRequest());
    // LPs now frozen until finalizeDefault / DefaultDistributor recovery
}
```

Key assertions to include: `expectedEpochInterest` delta equals `calcInterest(attackAmt) * (remaining + buffer) / (epochDuration + buffer)`, `underlying` balance of `borrower()` increases by `attackAmt` inside `depositDuringEpoch` (line 732), and `defaulted == true` after `stopEpoch` because `getFundsFromBorrower`'s `transferFrom` (line 552) exceeds the borrower's consented allowance, hitting the catch → `_handleBorrowerDefault` (line 504).

Relevant code: `depositDuringEpoch` forward + interest bump at contracts/IdleCDOEpochVariant.sol:691-732, borrower pull at 550-553/408, default at 501-505/577-599, gating at 656-669.