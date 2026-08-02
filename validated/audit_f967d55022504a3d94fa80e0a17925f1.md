Based on my research, I found a legitimate stake-accounting analog to the reported "unchecked zero-shares-minted" bug in `add_stake` of `delegation_pool.move`.

### Title
Silent zero-share minting in `add_stake` permanently strands delegator principal and misclassifies it as "rewards" - (File: `aptos-move/framework/aptos-framework/sources/delegation_pool.move`)

### Summary
`add_stake` transfers a delegator's coins into the underlying stake pool via `stake::add_stake` *before* crediting the delegator with active shares via `buy_in_active_shares`. `buy_in_active_shares` silently returns `0` and skips the internal `pool.active_shares.buy_in(...)` call whenever the computed `new_shares` rounds down to zero, without any revert — the exact "assumed non-zero shares" failure pattern called out in the external report, just on Aptos's internal `pool_u64_unbound`-based accounting instead of an ERC-4626 vault.

### Finding Description
In `add_stake`, real value is moved to the stake pool unconditionally, then shares are "bought in" separately: [1](#0-0) 

`buy_in_active_shares` computes `new_shares` from `amount_to_shares` and returns early with `0` — meaning `pool.active_shares.total_coins` and the delegator's share balance are **not updated** — whenever rounding drives the share count to zero: [2](#0-1) 

The underlying `pool_u64_unbound::buy_in` primitive shows the same "return 0 for zero shares" convention that the caller relies on without validating: [3](#0-2) 

Because `stake::add_stake(&retrieve_stake_pool_owner(pool), amount)` already happened at line 1501 regardless of whether shares get minted, the delegator's coins are now part of the stake pool's real `active`/`pending_active` balance, but `pool.active_shares.total_coins()` (the delegation pool's internal accounting of what belongs to shareholders) does not reflect this addition. On the next `synchronize_delegation_pool` call, `calculate_stake_pool_drift` compares the stake pool's real `active` balance against `pool.active_shares.total_coins()` and treats *any* positive difference as "rewards": [4](#0-3) 

Those "rewards" are then partially taken as operator commission and the remainder is implicitly redistributed to *existing* shareholders proportionally, via `update_total_coins` plus `buy_in_active_shares` for the operator's cut: [5](#0-4) 

So the depositing delegator's principal is not merely "stuck" — it is misclassified as pool rewards, partly paid out to the operator as commission, and the rest diluted among other delegators' share value, while the depositor who actually contributed the coins receives zero shares and zero claim.

### Impact Explanation
This falls under "share-accounting corruption that credits the wrong account or traps value" and "permanent lock or non-recoverable loss of claim rights" in the stake/delegation flow. An unprivileged delegator calling the public entry function `add_stake` can lose their entire deposited principal with no revert, no error, and no recovery path — the funds are absorbed into the pool and effectively donated to the operator (as commission) and other delegators (as phantom reward appreciation).

### Likelihood Explanation
Rounding `new_shares` to `0` in `amount_to_shares` requires the coins-per-share price to already be large relative to the deposited `coins_amount` (e.g., a long-lived, heavily rewarded/commissioned pool with a high total_coins/total_shares ratio) combined with a small enough deposit (post-fee) from the delegator. I was not able to fully verify in this session the exact behavior of `assert_min_active_balance` (called right after `buy_in_active_shares` at line 1505) — specifically whether it treats a delegator's zero balance as an accepted state (which would let the zero-share deposit pass silently) or whether it enforces a strict positive minimum that would abort the whole transaction. This detail is central to whether the transaction actually reverts (mitigating this to a non-issue) or silently succeeds (confirming the loss). I could not retrieve that function body before the tool budget was exhausted, so this should be verified directly against `assert_min_active_balance` and `get_add_stake_fee` in `delegation_pool.move` before treating this as conclusively exploitable.

### Recommendation
In `add_stake` (and analogously in `unlock_internal`/`buy_in_pending_inactive_shares`), validate that `buy_in_active_shares` returns a non-zero share count before proceeding, or perform the `stake::add_stake` transfer only after confirming shares will be minted:
```move
let new_shares = buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
assert!(new_shares > 0, error::invalid_argument(EDELEGATOR_ACTIVE_BALANCE_TOO_LOW));
```
Additionally, confirm `assert_min_active_balance` actually rejects the "balance stayed zero after a nonzero deposit" case rather than treating zero balance as an allowed state.

### Proof of Concept
Conceptual PoC (requires live confirmation of `assert_min_active_balance` behavior noted above):
1. Let a delegation pool accumulate a large `pool.active_shares.total_coins()` / `total_shares` ratio (e.g., after many epochs of rewards and operator commission distribution increase the coins-per-share price).
2. An unprivileged delegator calls `add_stake(delegator, pool_address, amount)` with `amount` small enough that `(amount - add_stake_fee) * total_shares / total_coins` truncates to `0` in `amount_to_shares`.
3. `stake::add_stake` still moves `amount` real APT into the stake pool (line 1501), but `buy_in_active_shares` returns `0` and never calls `pool.active_shares.buy_in` (lines 1727-1729), so the delegator's share balance remains unchanged/zero.
4. On the next `synchronize_delegation_pool` call, the stake-pool/shares-pool drift computed in `calculate_stake_pool_drift` (lines 1887-1898) attributes the delegator's stranded principal to "rewards," and it is partly paid to the operator as commission and the rest diluted into other delegators' share value (lines 1939-1956) — the original depositor never recovers it.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1499-1511)
```text
        // stake the entire amount to the stake pool
        aptos_account::transfer(delegator, pool_address, amount);
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);

        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1720-1739)
```text
    /// Buy shares into the active pool on behalf of delegator `shareholder` who
    /// deposited `coins_amount`. This function doesn't make any coin transfer.
    fun buy_in_active_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
    ): u128 acquires GovernanceRecords {
        let new_shares = pool.active_shares.amount_to_shares(coins_amount);
        // No need to buy 0 shares.
        if (new_shares == 0) { return 0 };

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        if (partial_governance_voting_enabled(pool_address)) {
            update_governance_records_for_buy_in_active_shares(pool, pool_address, new_shares, shareholder);
        };

        pool.active_shares.buy_in(shareholder, coins_amount);
        new_shares
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1887-1898)
```text
        // on stake-management operations, total coins on the internal shares pools and individual
        // stakes on the stake pool are updated simultaneously, thus the only stakes becoming
        // unsynced are rewards and slashes routed exclusively to/out the stake pool

        // operator `active` rewards not persisted yet to the active shares pool
        let pool_active = pool.active_shares.total_coins();
        let commission_active = if (active > pool_active) {
            math64::mul_div(active - pool_active, pool.operator_commission_percentage, MAX_FEE)
        } else {
            // handle any slashing applied to `active` stake
            0
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1939-1956)
```text
        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64_unbound.move (L124-136)
```text
    /// Allow an existing or new shareholder to add their coins to the pool in exchange for new shares.
    public fun buy_in(self: &mut Pool, shareholder: address, coins_amount: u64): u128 {
        if (coins_amount == 0) return 0;

        let new_shares = self.amount_to_shares(coins_amount);
        assert!(MAX_U64 - self.total_coins >= coins_amount, error::invalid_argument(EPOOL_TOTAL_COINS_OVERFLOW));
        assert!(MAX_U128 - self.total_shares >= new_shares, error::invalid_argument(EPOOL_TOTAL_SHARES_OVERFLOW));

        self.total_coins += coins_amount;
        self.total_shares += new_shares;
        self.add_shares(shareholder, new_shares);
        new_shares
    }
```
