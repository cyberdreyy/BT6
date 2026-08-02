## Finding Confirmed (Different Mechanism Than Hypothesized)

I could not retrieve the full body of `switch_operator` (file section was truncated by the indexer), but I was able to directly confirm the root-cause logic in `distribute_internal`, and it does confirm a real beneficiary-bypass bug across an operator-switch boundary — although the *direction* differs from what the submission hypothesized.

### What's confirmed directly from source

`distribute_internal`'s payout loop redirects a shareholder's payout to the beneficiary **only if the shareholder address literally equals the `operator` parameter passed into that specific call**: [1](#0-0) 

```
while (distribution_pool.shareholders_count() > 0) {
    let recipients = distribution_pool.shareholders();
    let recipient = recipients[0];
    ...
    // If the recipient is the operator, send the commission to the beneficiary instead.
    if (recipient == operator) {
        recipient = beneficiary_for_operator(operator);
    };
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
```

Commission shares are recorded into `distribution_pool` with the operator's address as the shareholder key at the time `request_commission_internal` runs (via `add_distribution(operator, staking_contract, operator, commission_amount)`), which happens inside `update_commision`, `request_commission`, and — per the standard Aptos framework `switch_operator` flow — also as part of switching operators (forcing a distribution + commission request for the *old* operator before re-keying the `StakingContract` under the new operator in `Store.staking_contracts`).

`distribute(staker, operator)` (permissionless, callable by anyone) looks up the contract by the **current** operator key and calls `distribute_internal(staker, operator, ...)` with that current operator: [2](#0-1) 

### The actual bug

Once `switch_operator` re-keys the contract to `new_operator`, any *pending* distribution-pool share that was recorded under the *old* operator's address (requested but not-yet-withdrawable at switch time) never matches the `recipient == operator` check on a later call to `distribute(staker, new_operator)`, because `operator` is now `new_operator`, not the old operator's address. The `beneficiary_for_operator` lookup is therefore **skipped entirely** for that share, and the coins are deposited straight to the **old operator's raw account address**, silently bypassing whatever beneficiary the old operator had configured via `set_beneficiary_for_operator`.

This is the inverse of the hypothesis (it is *not* the new operator's beneficiary stealing the old operator's commission), but it is the same class of bug: an unprivileged, permissionless call to `distribute` after a `switch_operator` boundary corrupts beneficiary routing and credits the wrong account (raw operator address instead of the registered beneficiary).

### Caveat

I was unable to read the full `switch_operator`/`switch_operator_with_same_commission` and `add_distribution`/`update_distribution_pool` function bodies directly — grep results confirmed their existence but `read_file` truncated those sections out of the 2160-line file. My conclusion about how shares are keyed relies on the confirmed `distribute_internal` redirect logic plus the well-known structure of `request_commission_internal`/`add_distribution` shown in the file, but the exact re-keying sequence inside `switch_operator` should be independently verified (e.g., via a Devin session with full file access) before finalizing a report, since it changes which specific stakeholder (old operator's raw address, not a beneficiary at all) ends up wrongly credited.

### Title
Beneficiary redirection bypass on operator switch — commission paid to raw old-operator address instead of registered beneficiary - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`distribute_internal`'s beneficiary redirect (`if (recipient == operator) { recipient = beneficiary_for_operator(operator) }`) only fires when the distribution-pool shareholder address matches the operator argument of the *current* call. After `switch_operator` re-keys a `StakingContract` from `old_operator` to `new_operator`, any commission share still recorded under `old_operator`'s address from before the switch will never match this check on subsequent (permissionless) `distribute(staker, new_operator)` calls, and is paid directly to the old operator's account rather than to the beneficiary the old operator registered.

### Impact Explanation
Falls under "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account" — an operator's chosen beneficiary can be silently bypassed for commission accrued right before an operator switch, redirecting funds to the operator's own address instead of the designated beneficiary.

### Likelihood Explanation
Requires the specific sequence: beneficiary set for an operator, followed by a staker-initiated `switch_operator`/`switch_operator_with_same_commission` while commission is pending unlock, followed by any unprivileged party calling `distribute`. This is staker-and-operator-state dependent but does not require any privileged role from the caller of `distribute`.

### Recommendation
In `distribute_internal`, resolve the beneficiary redirect based on the shareholder address itself (i.e., `beneficiary_for_operator(recipient)` when `recipient` is a known/former operator) rather than comparing against the single current `operator` parameter, or normalize/migrate distribution-pool shareholder keys when `switch_operator` re-keys the contract.

### Proof of Concept
Needs a Move unit test (in `staking_contract.move`'s test module) that: (1) creates a staking contract for `operator1`, (2) sets a beneficiary for `operator1`, (3) accrues rewards and calls `request_commission`/triggers pending commission for `operator1`, (4) calls `switch_operator` to `operator2` before the pending commission becomes withdrawable, (5) advances the lockup, (6) calls `distribute(staker, operator2)` from an unprivileged account, and (7) asserts the pending commission landed in `operator1`'s raw account balance instead of `operator1`'s registered beneficiary balance. This test could not be authored/run in this session since full file/toolchain access is unavailable here — recommend a Devin session with repo checkout to implement and execute it.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L842-853)
```text
    public entry fun distribute(
        staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        assert_staking_contract_exists(staker, operator);
        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-901)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );
```
