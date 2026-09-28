### Title
Post-default instant withdraw requests bypass the recovery-haircut validation applied to normal requests and are paid at par from the default recovery reserve — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
CVE-2019-9851 is a "validation applied to one execution path, missed on a parallel path" bug: LibreOffice blocked LibreLogo from document event handlers but forgot the equivalent global-script-event path. The analog is in `IdleCreditVault`: after `defaultRecoveryFinalized`, `requestWithdraw` explicitly validates and routes new receipts into `postDefaultRequests`, while `requestInstantWithdraw` has no post-default branch at all, so a new instant receipt is minted into `instantWithdrawsRequests[_user]` at full value and `claimInstantWithdrawRequest` pays it at par.

### Finding Description
In `requestWithdraw` (IdleCreditVault.sol:243-258), when `defaultRecoveryFinalized` is set, the function reverts if the user has any open receipt and otherwise records the amount in `postDefaultRequests[_user]` — a separate, already-haircut bucket that does not touch `pendingWithdraws`. This is the "document event handler" validation.

`requestInstantWithdraw` (IdleCreditVault.sol:356-375) performs none of these checks. It mints receipt tokens and increments `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, where `epochNumber` is now a post-default epoch that differs from `defaultRecoveryEpoch`.

On claim, `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393) first calls `_claimDefaultedInstantWithdrawRequest`, which only clears the receipt recorded at `defaultRecoveryEpoch` (line 844: `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`). The attacker's newly created post-default receipt survives in `instantWithdrawsRequests[_user]` and is paid out at par via `_transferFundedClaim(_user, amount)` — the same payout routine whose guard exists so that "funded" claims spend the correct reserve rather than being haircut. A receipt that was never funded by the borrower and never marked as a post-default request is paid 100% while all defaulted-epoch claimants receive only `defaultRecoveryPrice` of their basis via `_transferDefaultRecovery` (line 855).

The parallel-path asymmetry mirrors the CVE exactly: the post-default receipt validation was added to the normal withdraw path but the instant-withdraw path — a separate entry point reaching the same claim machinery — was not validated equivalently.

### Impact Explanation
Direct theft / dilution of the default recovery reserve. After default finalization, the strategy holds a fixed recovery reserve meant to be distributed pro-rata at `defaultRecoveryPrice`. An attacker (any tranche-token holder, including one who bought tranches cheaply post-default) calls `IdleCDOEpochVariant.requestWithdraw` while the instant-withdraw condition holds (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`, IdleCDOEpochVariant.sol:761-770), creating a par-valued receipt, then calls `claimInstantWithdrawRequest` (gated only by `allowInstantWithdraw`, IdleCDOEpochVariant.sol:975-979) and withdraws the full face amount. Loss is bounded by the reserve balance and is quantified as `min(requestAmount, defaultRecoveryReserve)`; every other defaulted claimant's payout is reduced correspondingly since the reserve is finite. The invariant broken is "one receipt, one recovery-priced payout": post-default receipts must either be blocked or priced at `defaultRecoveryPrice`, never at par.

### Likelihood Explanation
Requires `defaultRecoveryFinalized == true`, instant withdrawals enabled (`allowInstantWithdraw`), and an APR drop satisfying the instant-withdraw delta — all states that legitimately coexist after a default on a fixed-APR deployment where the manager has already lowered APR (a normal response to default). No privileged role acts maliciously; the attacker is an ordinary tranche holder. Caveat I could not fully resolve within the index: whether `requestWithdraw` in `IdleCDOEpochVariant` or `_ensureDefaultRecoveryInitialized` reverts for post-default instant requests on every deployment mode, and the exact funding source inside `_transferFundedClaim` (lines 897+) — if it draws from `defaultRecoveryReserve`, the theft is direct; if it can only spend separately escrowed funded amounts, impact reduces to accounting corruption/permanent freeze of later legitimate instant claims. Both outcomes are reportable; the PoC should confirm which reserve is debited.

### Recommendation
Mirror the normal-path validation in `requestInstantWithdraw`: when `defaultRecoveryFinalized` is set, either revert (instant withdraws are meaningless post-default since no borrower funding will occur) or route the receipt through a post-default pricing path equivalent to `postDefaultRequests`. At minimum, add the same "must claim open receipts first" check present at IdleCreditVault.sol:247-251 to the instant path, and make `claimInstantWithdrawRequest` clear any receipt recorded after `defaultRecoveryEpoch` against `defaultRecoveryPrice` rather than at par.

### Proof of Concept
Foundry fork/invariant PoC outline (structure follows `test/foundry/IdleCreditVault.t.sol` default tests, e.g. lines 4591-4616):

```solidity
function testPostDefaultInstantWithdrawPaysAtPar() external {
    // 1. deposit AA, run epoch 0, stop epoch 0
    // 2. victim requests normal withdraw; run epoch 1, borrower underfunds -> default
    // 3. manager calls finalizeDefault(recovered, manager); defaultRecoveryFinalized = true
    //    (see test at IdleCreditVault.t.sol:4591-4604)
    // 4. ensure allowInstantWithdraw and lastEpochApr > unscaledApr + delta
    //    (halve APR via setAprs as in test at line 3796)
    // 5. attacker (fresh tranche holder) calls cdoEpoch.requestWithdraw(amount, AAtranche)
    //    -> routes to requestInstantWithdraw, no defaultRecoveryFinalized branch
    // 6. attacker calls cdoEpoch.claimInstantWithdrawRequest()
    // assert: attacker received `amount` at par while victim's defaulted receipt
    // claims only amount * defaultRecoveryPrice / RECOVERY_FULL
    // assert: strategy underlying balance drops below the reserve needed to pay
    // remaining defaulted claims -> reserve drained / insolvency
}
```

Key assertion: `instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == 0`, so `_claimDefaultedInstantWithdrawRequest` skips the haircut, yet `instantWithdrawsRequests[attacker] != 0` is burned and paid at par.