No vulnerability found for this question.

**Rationale:**

The behavior described is explicitly documented, intentional design in both `delegation_pool.move` and `staking_contract.move`, not an unenforced invariant violation.

`synchronize_delegation_pool` computes commission lazily by diffing the stake pool's current `active`/`pending_inactive` totals against the delegation pool's last-known totals, then buys shares for `beneficiary_for_operator(operator)` **at the moment synchronize executes**, not at the moment the rewards accrued: [1](#0-0) 

The precondition that the current beneficiary should be paid out via a sync call *before* switching beneficiaries is explicitly documented at the call site and in the SDK builder: [2](#0-1) 

The identical pattern and identical documented caveat exists in `staking_contract.move`'s `set_beneficiary_for_operator` / `distribute_internal`, which explicitly says unpaid commission will go to the new beneficiary unless `distribute` is called first: [3](#0-2) [4](#0-3) 

Key points against treating this as a vulnerability:

1. **The two candidate beneficiaries (A and B) are both addresses chosen solely by the operator** via `set_beneficiary_for_operator`, which is correctly signer-gated to the operator. An unprivileged caller cannot introduce a third-party address to receive misrouted commission — they can only influence *timing* between two operator-selected destinations.
2. **`synchronize_delegation_pool` is permissionless by design** — it's meant to be callable by anyone (or triggered implicitly by any stake-management op like `add_stake`/`unlock`/`reactivate_stake`), since it just reflects real stake-pool state into the delegation pool's internal accounting. An unprivileged caller invoking it produces the exact same deterministic result as if the operator, a delegator, or the next epoch-boundary interaction had triggered it — there is no unique advantage gained by being "unprivileged."
3. The documented mitigation ("call synchronize/distribute before switching beneficiary") places the responsibility on the operator, a privileged role, to sequence their own actions correctly. The review's decision standard explicitly excludes findings that assume the attacker already holds "the pool, operator role, or governance authority" for the harmful state to materialize — here, the root cause is the operator's own failure to follow the documented sequencing, not a bypass of role checks or accounting invariants by an unprivileged actor.

No delegator funds, principal, or already-accrued delegator rewards are affected — only the operator's own commission is subject to this documented timing behavior.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1949-1956)
```text
        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/cached-packages/src/aptos_framework_sdk_builder.rs (L3426-3430)
```rust
/// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
/// beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool`
/// before switching the beneficiary. An operator can set one beneficiary for delegation pools, not a separate
/// one for each pool.
pub fn delegation_pool_set_beneficiary_for_operator(
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-809)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```
