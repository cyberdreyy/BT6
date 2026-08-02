## Analysis

The Sandclock bug pattern is: a `view` function performs unchecked subtraction (`totalUnderlying() - totalSponsored`), is depended upon by multiple other state-changing functions, and reverts on underflow whenever the subtrahend legitimately exceeds the minuend — bricking normal user flows (deposits) that have nothing to do with the excess condition.

I searched `stake.move`, `staking_contract.move`, `staking_proxy.move`, `delegation_pool.move`, and `vesting.move` for the same "shared unchecked-subtraction helper consumed by unprivileged entry points" shape. The strongest local analog is in `vesting.move`.

### Title
Unchecked subtraction in `total_accumulated_rewards()` can abort and DoS `unlock_rewards`/`vest`/`distribute` flows - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`total_accumulated_rewards()` computes vesting rewards with a raw, unchecked subtraction chain: `total_active_stake - vesting_contract.remaining_grant - commission_amount`. This function is a direct dependency of the permissionless entry points `unlock_rewards()` and `vest()` (which anyone can call for any vesting contract address, with no signer/admin check), exactly mirroring the Sandclock pattern where a shared accounting view function used by many callers can underflow-revert.

### Finding Description
`total_accumulated_rewards` reads the underlying `staking_contract`'s current active stake and commission, then subtracts the vesting contract's tracked `remaining_grant` and the freshly computed `commission_amount`: [1](#0-0) 

This is called unconditionally by: [2](#0-1) 
and transitively by `vest()`: [3](#0-2) 

Both `unlock_rewards` and `vest` are `public entry fun` taking only a `contract_address` — no caller-role check exists, so any unprivileged account can trigger this code path for any vesting contract.

The framework's own formal specification confirms this subtraction is not proven safe and documents the exact abort condition as a live, unresolved case: [4](#0-3) 

Notably the spec comment states *"This two item both contribute to the timeout"* — i.e., the prover could not establish `remaining_grant + commission_amount <= total_active_stake` always holds, and the condition is listed as an explicit `aborts_if`, not dismissed as unreachable.

The root of the risk is that `remaining_grant` (tracked purely inside `VestingContract`) and `total_active_stake`/`commission_amount` (derived independently from the underlying `staking_contract`'s `principal`/active balance) are two separately-maintained accounting values that are assumed, but not enforced, to stay consistent. Any divergence between them — e.g., through commission-rate changes, `staking_contract::request_commission` distributing funds out of the pool independently, or reward/commission recomputation timing differences — causes `total_active_stake < remaining_grant + commission_amount`, and the subtraction aborts.

### Impact Explanation
If `total_active_stake` falls below `remaining_grant + commission_amount`, every call to `unlock_rewards`, `vest`, and `distribute` (which calls `unlock_rewards` first) for that vesting contract reverts. Since `remaining_grant` can only be reduced by `vest()` itself (or zeroed on termination by the admin) and none of the permissionless functions have any way to reconcile the drift, the vesting contract's normal stake-unlock and reward-distribution paths become permanently unusable via the intended path, trapping the shareholders'/beneficiaries' unlock and distribution rights on the affected contract — matching the "permanent lock of claim rights in vesting flows" impact class. It does not directly enable theft/redirection, but it can strand legitimate value from being unlocked or claimed by shareholders/beneficiaries.

### Likelihood Explanation
Likelihood is moderate-to-uncertain: I could not, within the available context, fully trace every code path that updates `staking_contract.principal` (e.g., `request_commission`) versus `vesting_contract.remaining_grant` to construct a concrete, deterministic sequence of unprivileged calls that forces the underflow (unlike the Sandclock PoC, which had a concrete swap-fee scenario). The Move Prover itself times out on proving safety, and the `aborts_if` conditions in `vesting.spec.move` are asserted as real, not proven false — but I do not have a confirmed, minimal repro from local code alone establishing exact triggering steps. This is weaker evidence than a fully traced end-to-end unprivileged exploit.

### Recommendation
Guard the subtraction the same way the report recommends for Sandclock — clamp to zero (or explicitly reconcile `remaining_grant` against `total_active_stake` before subtracting) in `total_accumulated_rewards()`:
```move
public fun total_accumulated_rewards(vesting_contract_address: address): u64 acquires VestingContract {
    assert_active_vesting_contract(vesting_contract_address);
    let vesting_contract = borrow_global<VestingContract>(vesting_contract_address);
    let (total_active_stake, _, commission_amount) =
        staking_contract::staking_contract_amounts(vesting_contract_address, vesting_contract.staking.operator);
    let floor = vesting_contract.remaining_grant + commission_amount;
    if (floor > total_active_stake) { 0 } else { total_active_stake - floor }
}
```
Additionally, resolve/remove the `pragma verify = false` and outstanding `aborts_if` timeouts in `vesting.spec.move` for this function so the prover can positively confirm the invariant instead of merely documenting the abort condition.

### Proof of Concept
Not independently reconstructed with full confidence — I identified the vulnerable code, the exact unchecked-subtraction line, the unprivileged callers, and the spec-file evidence proving the abort condition is real, but I was unable, within the current investigation, to trace the precise unprivileged sequence of `staking_contract`/`vesting` calls (e.g., commission requests, lockup cycles) that forces `total_active_stake < remaining_grant + commission_amount` in practice. A Devin session with full repo/tooling access (Move Prover, unit test harness) would be needed to construct or refute a concrete PoC transaction sequence.

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L655-658)
```text
    public entry fun vest(contract_address: address) acquires VestingContract {
        // Unlock all rewards first, if any.
        unlock_rewards(contract_address);

```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L183-192)
```text
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
```
