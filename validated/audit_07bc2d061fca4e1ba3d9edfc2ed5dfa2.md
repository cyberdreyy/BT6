## Title
Distribution loop in `staking_contract::distribute_internal` can be permanently DoS'd by any past recipient who opts out of direct coin transfers, freezing staker's, operator's, and beneficiary's stake and commission - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract`'s payout logic pays every pending shareholder in a `StakingContract`'s `distribution_pool` (staker, operator, and any previously switched-out operators with pending commissions) via `aptos_account::deposit_coins` inside an unconditional `while` loop in `distribute_internal`. `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has an unregistered `CoinStore<AptosCoin>` and has opted out of arbitrary direct-coin transfers via `aptos_account::set_allow_direct_coin_transfers(false)`. Since `distribute_internal` is force-invoked at the start of `unlock_stake`, `request_commission`, `update_commision`, and `switch_operator`, any single stuck recipient permanently blocks the entire transaction for every other shareholder in that `StakingContract`, causing stranded/locked stake and commission funds. [1](#0-0) [2](#0-1) 

### Finding Description
This is a local Aptos analog of the external report's "unsafe external call assumed to always succeed" bug class. Instead of `ERC20.approve` silently failing on nonstandard tokens, Aptos's `aptos_account::deposit_coins` deliberately aborts when a recipient has disabled arbitrary coin acceptance and isn't yet registered:

```
public fun deposit_coins<CoinType>(to: address, coins: Coin<CoinType>) acquires DirectTransferConfig {
    ...
    if (!coin::is_account_registered<CoinType>(to)) {
        assert!(
            can_receive_direct_coin_transfers(to),
            error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
        );
        coin::register<CoinType>(&create_signer(to));
    };
    coin::deposit<CoinType>(to, coins)
}
``` [2](#0-1) 

`staking_contract::distribute_internal` withdraws all withdrawable coins for a `StakingContract` and pays out **every** shareholder currently registered in its `distribution_pool` in one atomic loop:

```
while (distribution_pool.shareholders_count() > 0) {
    ...
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
    ...
};
``` [1](#0-0) 

`distribution_pool` accumulates entries not just for the current staker but also for **past operators**: `request_commission_internal` adds a distribution entry keyed by `operator` for unpaid commission [3](#0-2) , and `switch_operator` re-keys the whole `StakingContract` (including its `distribution_pool`) under the new operator while the *old* operator can still have a pending, undistributed commission entry inside it [4](#0-3) . Crucially, `distribute_internal` is force-called as the very first step of `unlock_stake`, `request_commission`, `update_commision`, and `switch_operator` [5](#0-4) [6](#0-5) [7](#0-6) .

Because the loop is unconditional and atomic, a single unregistered+opted-out recipient (e.g., a former operator no longer active on the pool, or the current `beneficiary_for_operator`) causes the whole transaction — and every future call that reaches `distribute_internal` for that `StakingContract` — to abort. There is no way to skip, retry with a subset, or force-register the stuck recipient from within these entry functions.

### Impact Explanation
This breaks the "unlock, reactivate, withdraw ... paths must not redirect value or strand it permanently" invariant. Once a stuck recipient exists in a `StakingContract`'s `distribution_pool`, the honest staker can no longer:
- call `unlock_stake` to unlock any of their principal,
- call `request_commission`/`update_commision`,
- call `switch_operator`/`switch_operator_with_same_commission` to escape a malicious/unresponsive operator,

because all of these force `distribute_internal` first, which reverts. Any active stake, pending commission, and rewards accrued in that pool become permanently stuck (funds continue accruing rewards but can never be unlocked or withdrawn), matching the "Permanent lock or non-recoverable loss of claim rights in stake ... flows" impact category. The party causing the block need not hold any privileged role over the pool at the time of the block — a past operator who has since been switched out, or a beneficiary set via `set_beneficiary_for_operator`, is enough.

### Likelihood Explanation
Triggering this requires only a standard, publicly documented account action: calling `aptos_account::set_allow_direct_coin_transfers(false)` on an account that either never registers a `CoinStore<AptosCoin>` or does so after opting out. Any operator or beneficiary who is later switched out, disgruntled, or malicious can do this to grief the staker, and it requires no special access to the staking contract itself. The precondition (having a pending distribution entry) is naturally created any time an operator has unpaid commission at the moment of a `switch_operator` call, which is a normal/likely operational flow (rotating operators).

### Recommendation
- In `distribute_internal`, do not let one recipient's failed deposit revert the whole loop: wrap each `aptos_account::deposit_coins` call so failures are isolated per-recipient (e.g., check `coin::is_account_registered` / `aptos_account::can_receive_direct_coin_transfers` up front and, if the recipient cannot receive funds, retain their share instead of aborting the entire distribution), or maintain a per-recipient pending-claim mechanism (pull-based withdrawal) instead of a push loop.
- Alternatively, force-register recipients for `AptosCoin` at the time they are added to `distribution_pool` (in `add_distribution`), independent of their `DirectTransferConfig`, since payouts here are contractual obligations, not unsolicited transfers.

### Proof of Concept
1. Staker creates a staking contract with `operator_1` and joins the validator set; some epochs pass and commission accrues.
2. Staker calls `switch_operator_with_same_commission(staker, operator_1, operator_2)`. This calls `request_commission_internal` for `operator_1`, adding a pending distribution entry for `operator_1` in the (now `operator_2`-keyed) `StakingContract.distribution_pool`, per `switch_operator` at lines 783-805 of `staking_contract.move`.
3. Before the staker calls anything else, `operator_1` (no longer associated with the pool, fully unprivileged with respect to it) calls `aptos_account::set_allow_direct_coin_transfers(false)` and ensures it has no `CoinStore<AptosCoin>` registered (freshly created account, or one that never registered for APT).
4. The staker now calls `unlock_stake(staker, operator_2, amount)` (or `request_commission`, `update_commision`, `switch_operator` again). This force-invokes `distribute_internal`, which iterates `distribution_pool.shareholders()`, reaches `operator_1`, and calls `aptos_account::deposit_coins(operator_1, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire transaction reverts. Every subsequent call by the staker to `unlock_stake`, `request_commission`, `update_commision`, or `switch_operator` on this `StakingContract` will hit the same abort, permanently freezing the staker's principal and any unpaid commission/rewards in the pool.

Note: I was not able to fully verify from the indexed snippets whether `add_distribution`/`update_distribution_pool` contain any guard that removes or skips zero/invalid shareholders before the payout loop runs (the full body of `add_distribution` and `update_distribution_pool` was not returned by the search tools). Confirming there is no such mitigating check would require reading those functions in full — a Devin session with full repository access could verify this directly.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L564-590)
```text
    /// Convenience function to allow a staker to update the commission percentage paid to the operator.
    /// TODO: fix the typo in function name. commision -> commission
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );

        let staker_address = signer::address_of(staker);
        assert!(
            exists<Store>(staker_address),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-629)
```text
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-703)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
                operator,
                staking_contract,
            );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-805)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
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

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L111-131)
```text
    public fun deposit_coins<CoinType>(
        to: address, coins: Coin<CoinType>
    ) acquires DirectTransferConfig {
        if (!account::exists_at(to)) {
            create_account(to);
            spec {
                // TODO(fa_migration)
                // assert coin::spec_is_account_registered<AptosCoin>(to);
                // assume aptos_std::type_info::type_of<CoinType>() == aptos_std::type_info::type_of<AptosCoin>() ==>
                //     coin::spec_is_account_registered<CoinType>(to);
            };
        };
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
    }
```
