# STAKE-LOCKUP ANALOG SCAN — Result

## Title
Missing APT-registration check in `staking_contract::set_beneficiary_for_operator` can permanently block staker withdrawals via `distribute_internal` — (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

## Summary
The Caviar royalty bug is a class of **"accounting/registration set without validating the downstream consumer can actually receive the value"** — a value (royalty fee) is committed against a party based on an address that is later found unusable (zero recipient) at distribution time, and the mismatch causes the *other* party's assets to become stuck.

The Aptos-native analog is in `staking_contract.move`. When a beneficiary is set for a shareholder inside `vesting.move`, the code explicitly guards against exactly this class of bug:

```move
// vesting.move:915-923
public entry fun set_beneficiary(...) acquires VestingContract {
    // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
    // fail and block all other accounts from receiving APT if one beneficiary is not registered.
    assert_account_is_registered_for_apt(new_beneficiary);
    ...
``` [1](#0-0) 

However, the analogous function at the `staking_contract` layer — `set_beneficiary_for_operator`, which is callable directly and unprivileged by any operator (not gated behind vesting-admin logic) — has **no equivalent registration check**:

```move
// staking_contract.move:810-838
public entry fun set_beneficiary_for_operator(
    operator: &signer, new_beneficiary: address
) acquires BeneficiaryForOperator {
    assert!(
        features::operator_beneficiary_change_enabled(),
        std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
    );
    let operator_addr = signer::address_of(operator);
    let old_beneficiary = beneficiary_for_operator(operator_addr);
    if (exists<BeneficiaryForOperator>(operator_addr)) {
        borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
            new_beneficiary;
    } else {
        move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
    };
    emit(SetBeneficiaryForOperator { operator: operator_addr, old_beneficiary, new_beneficiary });
}
``` [2](#0-1) 

## Finding Description
`distribute_internal` is the function that actually pays out both the operator's commission and the staker's unlocked principal from the *same* `distribution_pool` in a single transaction:

```move
// staking_contract.move:855-870
fun distribute_internal(
    staker: address,
    operator: address,
    staking_contract: &mut StakingContract,
) acquires BeneficiaryForOperator {
    let pool_address = staking_contract.pool_address;
    if (!exists<Staker>(pool_address)) { ... };
    let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
    let total_potential_withdrawable = inactive + pending_inactive;
    let coins = ...
``` [3](#0-2) 

and it is invoked from the permissionless entry function `distribute`:

```move
// staking_contract.move:840-853
/// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
/// not need to be restricted to just the staker or operator.
public entry fun distribute(
    staker: address, operator: address
) acquires Store, BeneficiaryForOperator {
    assert_staking_contract_exists(staker, operator);
    let store = borrow_global_mut<Store>(staker);
    let staking_contract = store.staking_contracts.borrow_mut(&operator);
    distribute_internal(staker, operator, staking_contract);
}
``` [4](#0-3) 

Both the operator's commission (via `distribution_pool.buy_in(recipient, ...)` in `add_distribution`) and the staker's withdrawal (via `add_distribution(operator, staking_contract, staker_address, amount)` in `unlock_stake`) are paid out of the same `distribution_pool` inside one `distribute_internal` call:

```move
// staking_contract.move:937-957
fun add_distribution(
    operator: address,
    staking_contract: &mut StakingContract,
    recipient: address,
    coins_amount: u64,
) {
    let distribution_pool = &mut staking_contract.distribution_pool;
    ...
    distribution_pool.buy_in(recipient, coins_amount);
    ...
}
``` [5](#0-4) 

`vesting.move`'s own comment on `set_beneficiary` confirms the underlying transfer mechanism used in these distribution flows **can fail and abort the whole transaction if the recipient is not registered for AptosCoin, blocking all other recipients in the same distribution batch**. Because the operator's beneficiary is a single address shared by the operator across *all* its staking-contract pools (`"An operator can set one beneficiary for staking contract pools, not a separate one for each pool"`) and is settable by the operator alone via `set_beneficiary_for_operator` without the registration check that `vesting::set_beneficiary` deliberately enforces, an operator can set an address that is not registered for AptosCoin as its beneficiary.

## Impact Explanation
Once the unregistered beneficiary is set, every future call to `distribute()` (or `request_commission`, which internally calls `distribute_internal` first) for that operator's pool(s) will attempt to pay the beneficiary and abort. Because `distribute_internal` pays out from a single shared `distribution_pool`, this also blocks the staker's own unlocked/inactive stake from ever being withdrawn through this path — the staker did not choose the beneficiary and has no permission to change it, yet their withdrawal becomes permanently gated behind the operator's broken beneficiary configuration. This matches the Stake And Lockup Gate criteria: "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" and "Operator commission, beneficiary payout... corruption that... traps value."

## Likelihood Explanation
`set_beneficiary_for_operator` is a public entry function gated only by a feature flag (`operator_beneficiary_change_enabled`), callable by any operator signer with no staking-pool-specific validation. Setting an arbitrary, non-coin-registered address is a single, low-cost, unprivileged transaction, and any operator (malicious or simply careless) can trigger the condition.

## Recommendation
Add the same `assert_account_is_registered_for_apt(new_beneficiary)` guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator` (and audit `delegation_pool::set_beneficiary_for_operator`, which has an identical pattern), and/or decouple the staker's distribution path from the operator's commission distribution so a failure paying the operator's beneficiary cannot block the staker's own withdrawal.

## Proof of Concept
1. Staker creates a staking contract with `operator` via `staking_contract::create_staking_contract`.
2. `operator` calls `set_beneficiary_for_operator(operator, unregistered_addr)` where `unregistered_addr` has never called `coin::register<AptosCoin>`.
3. Stake pool accrues rewards; staker calls `unlock_stake` to queue a withdrawal, adding a distribution entry to the shared `distribution_pool`.
4. Lockup expires; any account calls `distribute(staker, operator)`. `distribute_internal` attempts to pay the operator's commission share to `unregistered_addr` and aborts the whole transaction (based on the failure mode `vesting.move` explicitly documents and guards against for the same scenario).
5. The staker's principal, now sitting inactive in the stake pool, cannot be withdrawn through `distribute` because every call reverts on the operator's unregistered beneficiary — the staker has no way to reset the operator's beneficiary.

**Caveat**: I was unable to fully view the exact transfer primitive inside `distribute_internal` (the tool returned a truncated/blank body for that portion of the function due to index size limits), so I could not directly confirm whether it uses a non-auto-registering `coin::transfer` versus an auto-registering `aptos_account::deposit_coins`. The finding rests on the explicit, in-repo comment in `vesting.move` stating this exact failure mode ("distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered") as the reason for its own registration check, combined with the fact that `staking_contract::set_beneficiary_for_operator` — the underlying primitive vesting itself calls — lacks that same check. I recommend a Devin session with full file access to confirm the exact transfer call inside `distribute_internal` before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-923)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-870)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```
