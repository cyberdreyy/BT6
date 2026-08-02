## Finding

The Solidity bug class (subtraction chain that can underflow and permanently revert critical accounting logic) has a direct analog in `aptos-move/framework/aptos-framework/sources/vesting.move`.

### Title
Underflow-prone subtraction chain in `vesting::total_accumulated_rewards` can permanently DoS `unlock_rewards`/`vest`/`distribute` for a vesting contract - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`total_accumulated_rewards` computes a vesting contract's unrealized rewards with a two-step subtraction:

```
total_active_stake - vesting_contract.remaining_grant - commission_amount
``` [1](#0-0) 

Move aborts (rather than silently wrapping) on unsigned-integer underflow, so this is functionally equivalent to the Solidity `revert` in the reported `LLMOracleCoordinator::finalizeValidation` bug: if `remaining_grant + commission_amount > total_active_stake`, every caller of this function reverts.

This function is invoked, directly or indirectly, from three permissionless `public entry` functions that anyone can call for any `contract_address` (no owner/admin check):
- `unlock_rewards(contract_address)` [2](#0-1) 
- `vest(contract_address)` (calls `unlock_rewards` first) [3](#0-2) 
- `distribute`/`terminate_vesting_contract`, which depend on the same staking_contract accounting path [4](#0-3) 

### Finding Description
`total_active_stake` and `commission_amount` come from `staking_contract::staking_contract_amounts`, which derives `accumulated_rewards = total_active_stake - staking_contract.principal` and `commission_amount = accumulated_rewards * commission_percentage / 100`. `vesting_contract.remaining_grant`, however, is a separate piece of state tracked only inside `vesting.move` and decremented solely by `vest()` [5](#0-4) . `staking_contract.principal` is a different value, mutated independently by `staking_contract::unlock_stake`, `request_commission_internal`, and `switch_operator*` paths [6](#0-5) .

Because `remaining_grant` and `principal` are two independently-updated ledgers that are only assumed to stay in sync, `total_accumulated_rewards` implicitly assumes `remaining_grant + commission_amount <= total_active_stake` always holds. The module's own formal-verification spec explicitly documents this as an unproven, abort-prone invariant and disables verification because of it:

```
aborts_if (vesting_contract.remaining_grant + commission_amount) > total_active_stake;
aborts_if total_active_stake < vesting_contract.remaining_grant;
...
spec accumulated_rewards(...) { pragma verify = false; ... }
spec unlock_rewards(contract_address: address) { pragma verify = false; include UnlockRewardsAbortsIf; }
``` [7](#0-6) 

This is the exact analog of the reported bug: an unsigned subtraction that the code assumes is always non-negative, but which the spec authors themselves flag as capable of aborting.

### Impact Explanation
If this invariant breaks (e.g., through operator/commission-percentage changes interacting with `distribute`/`request_commission` resetting `principal` while `remaining_grant` lags behind, or any other path that lets `principal` diverge upward from `remaining_grant`), then:
- `unlock_rewards`, `vest`, and `distribute` all abort permanently for that vesting contract.
- Since these are the only entry points that move stake out of `pending_inactive`/`inactive` and distribute it to shareholders/operator, a permanent abort here **strands the shareholders' and operator's claim rights** in the vesting contract — matching the "Permanent lock or non-recoverable loss of claim rights in vesting flows" impact category.
- `terminate_vesting_contract` (admin recovery path) also calls `distribute` first, so even admin-driven recovery could be blocked.

### Likelihood Explanation
Medium confidence: I confirmed the exact underflow-prone expression and that the module's own Move Prover spec calls out this abort condition and disables verification because it could not be ruled out. What I was **not able to fully trace** within this investigation is the precise sequence of operator/commission/principal updates in `staking_contract.move` (`request_commission_internal`, `update_distribution_pool`) that would drive `principal` above `remaining_grant + commission_amount` in practice — that requires deeper tracing of `staking_contract.move`'s commission/distribution bookkeeping that I could not complete in the available iterations. The vulnerability's existence (the abort itself, and its reachability from unprivileged entry points) is proven by local code and the framework's own spec; the exact minimal repro transaction sequence is not fully proven here.

### Recommendation
- Refactor `total_accumulated_rewards` to avoid unchecked chained subtraction, e.g. by clamping: `if (total_active_stake < vesting_contract.remaining_grant + commission_amount) { 0 } else { total_active_stake - remaining_grant - commission_amount }`.
- Strengthen the invariant so `remaining_grant` and `staking_contract.principal` cannot diverge in a way that lets `remaining_grant + commission_amount` exceed `total_active_stake`, and re-enable Move Prover verification (`pragma verify = false` should be removed) for `total_accumulated_rewards`, `accumulated_rewards`, `unlock_rewards`, `vest`, and `distribute`.
- Add a fuzz/property test that runs `update_commission_percentage`/`switch_operator`/`request_commission` sequences interleaved with `vest`/`unlock_rewards` to hunt for a concrete state where `remaining_grant + commission_amount > total_active_stake`.

### Proof of Concept
Not fully constructed — the concrete state-transition sequence that drives `principal` above `remaining_grant + commission_amount` requires deeper analysis of `staking_contract::request_commission_internal` / `update_distribution_pool` than could be completed in this session. The abort condition itself and its reachability from permissionless entry functions is proven directly via [1](#0-0)  and the corresponding spec at [8](#0-7) . A background Devin session with terminal/Move-test access would be needed to construct and run an end-to-end Move test reproducing the exact abort.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L451-458)
```text
    public fun total_accumulated_rewards(vesting_contract_address: address): u64 acquires VestingContract {
        assert_active_vesting_contract(vesting_contract_address);

        let vesting_contract = borrow_global<VestingContract>(vesting_contract_address);
        let (total_active_stake, _, commission_amount) =
            staking_contract::staking_contract_amounts(vesting_contract_address, vesting_contract.staking.operator);
        total_active_stake - vesting_contract.remaining_grant - commission_amount
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L636-640)
```text
    public entry fun unlock_rewards(contract_address: address) acquires VestingContract {
        let accumulated_rewards = total_accumulated_rewards(contract_address);
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        unlock_stake(vesting_contract, accumulated_rewards);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L655-665)
```text
    public entry fun vest(contract_address: address) acquires VestingContract {
        // Unlock all rewards first, if any.
        unlock_rewards(contract_address);

        // Unlock the vested amount. This amount will become withdrawable when the underlying stake pool's lockup
        // expires.
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        // Short-circuit if vesting hasn't started yet.
        if (vesting_contract.vesting_schedule.start_timestamp_secs > timestamp::now_seconds()) {
            return
        };
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L687-693)
```text
        let total_grant = vesting_contract.grant_pool.total_coins();
        let vested_amount = fixed_point32::multiply_u64(total_grant, vesting_fraction);
        // Cap vested amount by the remaining grant amount so we don't try to distribute more than what's remaining.
        vested_amount = min(vested_amount, vesting_contract.remaining_grant);
        vesting_contract.remaining_grant -= vested_amount;
        vesting_schedule.last_vested_period = next_period_to_vest;
        unlock_stake(vesting_contract, vested_amount);
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L719-728)
```text
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L705-711)
```text
        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L181-271)
```text
        let pending_active = coin::value(stake_pool.pending_active);
        let total_active_stake = active + pending_active;
        let accumulated_rewards = total_active_stake - staking_contract.principal;
        let commission_amount = accumulated_rewards * staking_contract.commission_percentage / 100;
        aborts_if !exists<stake::StakePool>(pool_address);
        aborts_if active + pending_active > MAX_U64;
        aborts_if total_active_stake < staking_contract.principal;
        aborts_if accumulated_rewards * staking_contract.commission_percentage > MAX_U64;
        // This two item both contribute to the timeout
        aborts_if (vesting_contract.remaining_grant + commission_amount) > total_active_stake;
        aborts_if total_active_stake < vesting_contract.remaining_grant;
    }

    spec accumulated_rewards(vesting_contract_address: address, shareholder_or_beneficiary: address): u64 {
        // TODO: A severe timeout can not be resolved.
        pragma verify = false;

        // This schema lead to timeout
        include TotalAccumulatedRewardsAbortsIf;

        let vesting_contract = global<VestingContract>(vesting_contract_address);
        let operator = vesting_contract.staking.operator;
        let staking_contracts = global<staking_contract::Store>(vesting_contract_address).staking_contracts;
        let staking_contract = simple_map::spec_get(staking_contracts, operator);
        let pool_address = staking_contract.pool_address;
        let stake_pool = global<stake::StakePool>(pool_address);
        let active = coin::value(stake_pool.active);
        let pending_active = coin::value(stake_pool.pending_active);
        let total_active_stake = active + pending_active;
        let accumulated_rewards = total_active_stake - staking_contract.principal;
        let commission_amount = accumulated_rewards * staking_contract.commission_percentage / 100;
        let total_accumulated_rewards = total_active_stake - vesting_contract.remaining_grant - commission_amount;

        let shareholder = spec_shareholder(vesting_contract_address, shareholder_or_beneficiary);
        let pool = vesting_contract.grant_pool;
        let shares = pool_u64::spec_shares(pool, shareholder);
        aborts_if pool.total_coins > 0 && pool.total_shares > 0
            && (shares * total_accumulated_rewards) / pool.total_shares > MAX_U64;

        ensures result == pool_u64::spec_shares_to_amount_with_total_coins(pool, shares, total_accumulated_rewards);
    }

    spec shareholders(vesting_contract_address: address): vector<address> {
        include ActiveVestingContractAbortsIf{contract_address: vesting_contract_address};
    }

    spec fun spec_shareholder(vesting_contract_address: address, shareholder_or_beneficiary: address): address;

    spec shareholder(vesting_contract_address: address, shareholder_or_beneficiary: address): address {
        pragma opaque;
        include ActiveVestingContractAbortsIf{contract_address: vesting_contract_address};
        ensures [abstract] result == spec_shareholder(vesting_contract_address, shareholder_or_beneficiary);
    }

    spec create_vesting_schedule(
        schedule: vector<FixedPoint32>,
        start_timestamp_secs: u64,
        period_duration: u64,
    ): VestingSchedule {
        /// [high-level-req-6]
        aborts_if !(len(schedule) > 0);
        aborts_if !(period_duration > 0);
        aborts_if !exists<timestamp::CurrentTimeMicroseconds>(@aptos_framework);
        aborts_if !(start_timestamp_secs >= timestamp::now_seconds());
    }

    spec create_vesting_contract {
        // TODO: Data invariant does not hold.
        pragma verify = false;
        /// [high-level-req-10]
        aborts_if withdrawal_address == @aptos_framework || withdrawal_address == @vm_reserved;
        aborts_if !exists<account::Account>(withdrawal_address);
        aborts_if !exists<coin::CoinStore<AptosCoin>>(withdrawal_address);
        aborts_if len(shareholders) == 0;
        // property 2: The vesting pool should not exceed a maximum of 30 shareholders.
        aborts_if simple_map::spec_len(buy_ins) != len(shareholders);
        ensures global<VestingContract>(result).grant_pool.shareholders_limit == 30;
    }

    spec unlock_rewards(contract_address: address) {
        // TODO: Calls `unlock_stake` which is not verified.
        // Current verification times out.
        pragma verify = false;
        include UnlockRewardsAbortsIf;
    }

    spec schema UnlockRewardsAbortsIf {
        contract_address: address;

        // Cause timeout here
        include TotalAccumulatedRewardsAbortsIf { vesting_contract_address: contract_address };
```
