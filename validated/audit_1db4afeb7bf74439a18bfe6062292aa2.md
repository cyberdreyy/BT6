## Title
Post-switch commission shares are re-taxed and misallocated by `update_distribution_pool` after `switch_operator` — ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::switch_operator` migrates the entire `StakingContract` struct — including its `distribution_pool` — from the `old_operator` key to the `new_operator` key while preserving any pending distribution shares already bought in by `old_operator` (its unpaid commission recorded via `request_commission_internal`/`add_distribution`) [1](#0-0) . However, `distribute_internal`/`update_distribution_pool` charges commission on "all shareholders except `operator`" using the *current* operator address only [2](#0-1) . After a switch, the old operator's already-realized commission balance sitting in the shared `distribution_pool` is no longer exempt (since `shareholder != operator` now evaluates true for the ex‑operator), so any further reward growth on the pool causes the old operator's commission-share balance to be taxed again on behalf of the new operator.

### Finding Description
`switch_operator` performs, in order:
1. Remove the `StakingContract` from `old_operator`'s slot.
2. `distribute_internal(staker, old_operator, &mut staking_contract)` — settles withdrawable funds; correctly exempts `old_operator` in `update_distribution_pool`.
3. `request_commission_internal(old_operator, &mut staking_contract)` — computes newly accrued commission and calls `add_distribution(old_operator, staking_contract, old_operator, commission_amount)`, buying the old operator shares in the same `distribution_pool` at the current total-coins snapshot [3](#0-2) .
4. `stake::set_operator_with_cap(..., new_operator)` and `staking_contract.commission_percentage = new_commission_percentage`.
5. The **same** `StakingContract` object (same `distribution_pool`, still holding `old_operator`'s just-bought commission shares) is re-inserted under `new_operator` [4](#0-3) .

From this point on, every call that touches the pool (`unlock_stake`, `request_commission`, `distribute`) invokes `distribute_internal`/`update_distribution_pool` with `operator = new_operator`. The exemption check `if (shareholder != operator)` in `update_distribution_pool` no longer recognizes `old_operator` as an operator — it is just another shareholder now — so any rewards that accrue on the pool between the switch and the next distribution cause `old_operator`'s already-settled commission-share balance to be commission-taxed a second time under the new operator's `commission_percentage`, at the new operator's benefit. This effectively corrects operator-share exemption at the wrong entity: value that legitimately belonged to the ex-operator (recorded as principal-adjusted commission) is silently reduced (further commissioned) and that skimmed value flows to the new distribution pool for the benefit of everyone else / the new operator's accounting, i.e., a wrong-recipient/corrupted-share accounting bug in the exact sense targeted by the "signed field vs. checked field mismatch" bug class: `update_distribution_pool` "signs off" only on the *current* operator identity, not on which shareholder actually holds already-taxed commission shares.

Additionally, `distribute_internal`'s beneficiary-redirect logic (`if (recipient == operator) { recipient = beneficiary_for_operator(operator) }`) also keys off the *current* `operator` parameter [5](#0-4) . Since `old_operator != new_operator`, the ex‑operator's commission distribution is paid directly to the `old_operator` account instead of being routed to whatever beneficiary `old_operator` had configured via `set_beneficiary_for_operator`, silently bypassing the beneficiary-redirection guarantee documented in `set_beneficiary_for_operator`'s comment ("An operator can set one beneficiary...") [6](#0-5) .

### Impact Explanation
This corrupts operator-commission share accounting for a real, mainnet-reachable, unprivileged flow (any staker can call `switch_operator`/`switch_operator_with_same_commission` at any time, no special privilege needed) [7](#0-6) . It causes value belonging to the outgoing operator to be re-taxed and diverted, and separately causes the outgoing operator's beneficiary-routing guarantee to be silently dropped, redirecting commission payouts to the wrong account. This matches the "Operator commission ... share-accounting corruption that credits the wrong account or traps value" and "Wrong-role control ... without already holding that role" impact classes in scope.

### Likelihood Explanation
The trigger requires only: (1) an active staking_contract with nonzero commission (default flow), (2) the staker calling `switch_operator`/`switch_operator_with_same_commission` — a normal, permissionless, publicly documented operation, and (3) any reward accrual occurring on the pool between the switch and the subsequent `distribute`/`unlock_stake`/`request_commission` call, which happens automatically every epoch as long as the pool is active. No adversarial coordination or privileged role is needed beyond being the staker of one's own staking contract, and epochal reward accrual is guaranteed on any active validator. This makes the likelihood high for any staker who ever switches operators while an operator-commission balance is outstanding.

### Recommendation
When migrating a `StakingContract` to a new operator in `switch_operator`, either:
- Force-settle (via `distribute_internal` + full withdrawal/payout) all pending distribution shares belonging to `old_operator` before reassigning the struct, removing `old_operator`'s shares from the shared `distribution_pool` entirely, or
- Track the *original* commission-recipient address per share (not just the live "current operator" field) so `update_distribution_pool`'s exemption check and `distribute_internal`'s beneficiary-redirect check compare against the operator address that was in effect when the shares were bought in, not the pool's current `operator` field.

### Proof of Concept
1. Staker creates a staking contract with `operator_A`, commission 10%.
2. Rewards accrue over several epochs; `operator_A` calls `request_commission`, which buys shares for `operator_A` in `distribution_pool` (exempt from commission because `shareholder == operator` at that time).
3. Staker calls `switch_operator(staker, operator_A, operator_B, new_commission)`. Internally: `distribute_internal`/`request_commission_internal` settle `operator_A`'s latest commission and buy additional exempt shares for `operator_A`; the whole `StakingContract` (with `operator_A`'s shares still in `distribution_pool`) is moved to key `operator_B`.
4. More epochs pass, rewards accrue on the stake pool.
5. Staker calls `unlock_stake`/`distribute` (which resolves to `distribute_internal(staker, operator_B, ...)`): `update_distribution_pool(distribution_pool, ..., operator=operator_B, commission_percentage)` iterates all shareholders, and since `operator_A != operator_B`, `operator_A`'s previously-exempt commission-share balance is now charged additional commission on its growth — value that should remain 100% `operator_A`'s balance shrinks, with the taxed amount effectively benefiting the pool at large / `operator_B`'s accounting context.
6. When `operator_A`'s remaining distribution is eventually paid out via `distribute_internal`, `recipient == operator` compares against `operator_B`, so the payment is *not* redirected to `operator_A`'s configured beneficiary (set via `staking_contract::set_beneficiary_for_operator`), sending funds to `operator_A`'s primary account instead of the intended beneficiary.

Note: I was not able to run the Move test suite in this environment to numerically confirm the exact skimmed amount; the trace above is based on static analysis of `switch_operator`, `distribute_internal`, `update_distribution_pool`, and `request_commission_internal`. A Devin session with `move test` tooling would be needed to produce concrete numeric confirmation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-657)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L746-805)
```text
    public entry fun switch_operator_with_same_commission(
        staker: &signer, old_operator: address, new_operator: address
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        let commission_percentage = commission_percentage(staker_address, old_operator);
        switch_operator(
            staker,
            old_operator,
            new_operator,
            commission_percentage
        );
    }

    /// Allows staker to switch operator without going through the lenghthy process to unstake.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1001-1020)
```text
    fun update_distribution_pool(
        distribution_pool: &mut Pool,
        updated_total_coins: u64,
        operator: address,
        commission_percentage: u64
    ) {
        // Short-circuit and do nothing if the pool's total value has not changed.
        if (distribution_pool.total_coins() == updated_total_coins) { return };

        // Charge all stakeholders (except for the operator themselves) commission on any rewards earnt relatively to the
        // previous value of the distribution pool.
        let shareholders = &distribution_pool.shareholders();
        shareholders.for_each_ref(
            |shareholder| {
                let shareholder: address = *shareholder;
                if (shareholder != operator) {
                    let shares = pool_u64::shares(distribution_pool, shareholder);
                    let previous_worth = pool_u64::balance(distribution_pool, shareholder);
                    let current_worth =
                        pool_u64::shares_to_amount_with_total_coins(
```
