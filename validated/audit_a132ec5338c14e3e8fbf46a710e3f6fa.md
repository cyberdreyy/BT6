No vulnerability found for this question.

**Rationale:**

`withdraw` derives `delegator_address` from the transaction signer only [1](#0-0) , and there is no parameter allowing the caller to specify another delegator's address or an arbitrary `observed_lockup_cycle`. The OLC used for a withdrawal is not attacker-controlled — it is looked up internally via `pending_withdrawal_exists(pool, delegator_address)`, which reads the caller's *own* entry in `pool.pending_withdrawals` keyed by their own address [2](#0-1) .

`redeem_inactive_shares` then redeems shares from `pool.inactive_shares.borrow(lockup_cycle)` using `shareholder = delegator_address` (still the caller) [3](#0-2) . The amount to redeem is capped via `amount_to_shares_to_redeem`, which caps at `shares_pool.balance(shareholder)`/`shares(shareholder)` for that specific shareholder [4](#0-3) . If the caller owns no shares in that OLC's pool (because it belongs to a different delegator's withdrawal), `shares_to_redeem` evaluates to `0` and the function silently returns `0`, withdrawing nothing rather than draining another delegator's balance [5](#0-4) .

This exact scenario — a delegator attempting to withdraw stake unlocked by someone else — is explicitly covered by an existing test that asserts the withdrawing account's balance remains `0` afterward: [6](#0-5) .

Because share accounting inside `inactive_shares`/`pending_inactive` pools is strictly per-shareholder (keyed by the actual delegator address, not attacker-suppliable), and the OLC is derived from the caller's own recorded pending withdrawal rather than any external input, there is no path for an unprivileged delegator to redeem or drain another delegator's inactive shares via `withdraw`. The described broken invariant does not actually exist in this code — the accounting invariant (own shares only, own OLC only) is enforced by construction, not merely by a checked assertion that could be bypassed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1614-1620)
```text
    public entry fun withdraw(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert!(amount > 0, error::invalid_argument(EWITHDRAW_ZERO_STAKE));
        // synchronize delegation and stake pools before any user operation
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1690-1696)
```text
    fun pending_withdrawal_exists(pool: &DelegationPool, delegator_address: address): (bool, ObservedLockupCycle) {
        if (pool.pending_withdrawals.contains(delegator_address)) {
            (true, *pool.pending_withdrawals.borrow(delegator_address))
        } else {
            (false, olc_with_index(0))
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1777-1788)
```text
    fun amount_to_shares_to_redeem(
        shares_pool: &pool_u64::Pool,
        shareholder: address,
        coins_amount: u64,
    ): u128 {
        if (coins_amount >= shares_pool.balance(shareholder)) {
            // cap result at total shares of shareholder to pass `EINSUFFICIENT_SHARES` on subsequent redeem
            shares_pool.shares(shareholder)
        } else {
            shares_pool.amount_to_shares(coins_amount)
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1818-1845)
```text
    fun redeem_inactive_shares(
        pool: &mut DelegationPool,
        shareholder: address,
        coins_amount: u64,
        lockup_cycle: ObservedLockupCycle,
    ): u64 acquires GovernanceRecords {
        let shares_to_redeem = amount_to_shares_to_redeem(
            pool.inactive_shares.borrow(lockup_cycle),
            shareholder,
            coins_amount);
        // silently exit if not a shareholder otherwise redeem would fail with `ESHAREHOLDER_NOT_FOUND`
        if (shares_to_redeem == 0) return 0;

        // Always update governance records before any change to the shares pool.
        let pool_address = get_pool_address(pool);
        // Only redeem shares from the pending_inactive pool at `lockup_cycle` == current OLC.
        if (partial_governance_voting_enabled(pool_address) && lockup_cycle.index == pool.observed_lockup_cycle.index) {
            update_governanace_records_for_redeem_pending_inactive_shares(
                pool,
                pool_address,
                shares_to_redeem,
                shareholder
            );
        };

        let inactive_shares = pool.inactive_shares.borrow_mut(lockup_cycle);
        // 1. reaching here means delegator owns inactive/pending_inactive shares at OLC `lockup_cycle`
        let redeemed_coins = inactive_shares.redeem_shares(shareholder, shares_to_redeem);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2801-2803)
```text
        // cannot withdraw stake unlocked by others
        withdraw(delegator, pool_address, 50 * ONE_APT);
        assert!(coin::balance<AptosCoin>(delegator_address) == 0, 0);
```
