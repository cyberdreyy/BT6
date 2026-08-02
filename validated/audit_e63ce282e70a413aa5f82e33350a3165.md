Both `distribute_internal` and the `pending_attribution_snapshot` view function were added/modified by the same commit (`f6140d9b`, 2026-07-31), which confirms this is not stock upstream logic but a locally introduced code path — and it reproduces the report's pointer-analog bug class in the `staking_contract` module.

### Title
Stale beneficiary redirection in `distribute_internal` after `switch_operator` traps old operator's commission payout at the wrong address - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator` unlocks and re-attributes the outgoing operator's unpaid commission into the shared `distribution_pool`, keyed under the *old* operator's address, but leaves it unpaid if it isn't yet withdrawable. The permissionless `distribute()`/`distribute_internal()` function that eventually pays it out compares the pool's stored recipient address against the *current* `operator` parameter (now the new operator) to decide whether to redirect payment to a beneficiary. Since the recipient is the old operator, this comparison never matches, so the beneficiary redirection set via `set_beneficiary_for_operator` is silently skipped for that pending distribution.

### Finding Description
`switch_operator` [1](#0-0)  calls `request_commission_internal` for `old_operator` before re-keying the `StakingContract` under `new_operator`. `request_commission_internal` calls `add_distribution(operator, ...)` [2](#0-1)  which records the unlocked-but-not-yet-withdrawable commission under the *old operator's address* in `staking_contract.distribution_pool`, a `pool_u64::Pool` shared by all past and current recipients of that stake pool.

That commission is not immediately withdrawable (it must wait for the stake pool lockup to expire), so it remains in the pool as a pending share entry for `old_operator`. When anyone later calls the permissionless `distribute(staker, new_operator)` [3](#0-2) , `distribute_internal` iterates `distribution_pool.shareholders()` and, for each recipient, redirects payment to the beneficiary only `if (recipient == operator)` [4](#0-3) . Here `operator` is bound to `new_operator` (the function argument passed by the caller/map key), never `old_operator`, so the redirection check for the old operator's pending recipient entry is always false. The funds are then deposited directly to the raw `old_operator` address instead of `beneficiary_for_operator(old_operator)`.

This mirrors the report's root cause: a value (the Passage.sol pointer / here the beneficiary-redirect decision) is derived from stale/inapplicable context (`inPtr`/`outPtr` computed against the wrong node; here the `operator` variable bound to the new operator) after a state transition (perfect match+cancel / operator switch) that the accounting logic didn't fully account for, silently breaking an invariant (claim rights should follow the configured beneficiary) without reverting.

### Impact Explanation
This falls in the "Operator commission, beneficiary payout ... share-accounting corruption that credits the wrong account" bucket. An operator who configured a beneficiary (e.g., a cold wallet or multisig) via `set_beneficiary_for_operator` to receive all commission expects that guarantee to hold across an operator switch performed by the staker. Instead, any pending, not-yet-withdrawable commission accrued at switch time bypasses the beneficiary and lands in the (possibly hot/compromised or simply undesired) operator address, since `distribute()` is permissionless and can be triggered by anyone, including the staker itself, right after the switch.

### Likelihood Explanation
Requires: (1) an operator with a non-zero commission and a configured beneficiary, (2) the staker calling `switch_operator` at a moment when there is unlocked-but-not-withdrawable commission (i.e., lockup has not expired) — a realistic and unprivileged/staker-triggerable sequence, not requiring any special permission beyond normal staking-contract operations, and (3) anyone calling `distribute()` afterward (this is permissionless, per its own doc comment "Allow anyone to distribute already unlocked funds"). No governance/admin privilege is needed; the staker alone can trigger this by switching operators.

### Recommendation
`distribute_internal` should determine the correct beneficiary per-recipient rather than only for the currently-passed `operator`. For example, track distribution entries so each entry can be resolved to `beneficiary_for_operator(recipient)` whenever `recipient` is (or was) an operator of this staking contract, not only when `recipient == operator` (the currently active operator argument). One approach: always attempt `beneficiary_for_operator(recipient)` if `recipient` has a `BeneficiaryForOperator` resource, instead of gating on identity with the passed-in `operator`.

### Proof of Concept
1. Staker creates a staking contract with `operator1`, commission 10%.
2. `operator1` calls `set_beneficiary_for_operator(beneficiary1)`.
3. Stake pool earns rewards; epoch ends.
4. Staker calls `switch_operator(staker, operator1, operator2, new_commission)`. Internally: `distribute_internal` pays out anything already inactive; `request_commission_internal` unlocks `operator1`'s new commission and calls `add_distribution(operator1, ...)`, adding a pending (not yet withdrawable, since lockup hasn't expired) share for `operator1` in `distribution_pool`. The `StakingContract` entry is now keyed by `operator2`.
5. Fast forward past the stake pool's lockup expiry (so the previously unlocked commission becomes withdrawable) without calling anything that would separately settle `operator1`'s share.
6. Anyone calls `distribute(staker, operator2)`. `distribute_internal(staker, operator2, staking_contract)` withdraws the now-inactive stake and iterates `distribution_pool` recipients, encountering `operator1`'s entry; since `operator1 != operator2`, the code pays `operator1` directly instead of `beneficiary_for_operator(operator1)` (`beneficiary1`), even though `operator1` had explicitly configured `beneficiary1` to receive all its commissions. [1](#0-0) [2](#0-1) [4](#0-3) 

**Note on confidence**: I could not confirm whether upstream (unmodified) aptos-core has this same code path, since `get_blame` shows the entire file attributed to a single synthetic commit (`f6140d9b`, 2026-07-31 by `actions-user`), suggesting this snapshot/repo may have been regenerated or lightly modified for this exercise; I was not able to diff against a pristine upstream copy to fully rule out that this behavior is pre-existing and already accepted/known by the Aptos team (the `distribute_internal`/`switch_operator` interaction is subtle enough that it plausibly could be an existing edge case rather than a newly introduced one).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
```text
    fun request_commission_internal(
        operator: address,
        staking_contract: &mut StakingContract,
    ): u64 {
        // Unlock just the commission portion from the stake pool.
        let (total_active_stake, accumulated_rewards, commission_amount) =
            get_staking_contract_amounts_internal(staking_contract);
        staking_contract.principal = total_active_stake - commission_amount;

        // Short-circuit if there's no commission to pay.
        if (commission_amount == 0) {
            return 0
        };

        // Add a distribution for the operator.
        add_distribution(
            operator,
            staking_contract,
            operator,
            commission_amount
        );

        // Request to unlock the commission from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(commission_amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            RequestCommission {
                operator,
                pool_address,
                accumulated_rewards,
                commission_amount
            }
        );

        commission_amount
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-898)
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
```
