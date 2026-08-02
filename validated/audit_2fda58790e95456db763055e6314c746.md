## Title
Single non-consenting shareholder can permanently block `staking_contract::distribute` / `distribute_internal`, freezing all other shareholders' unlocked stake and operator commission - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`distribute_internal` in `staking_contract.move` withdraws *all* currently-inactive stake from the underlying stake pool in a single call, then loops over every recipient in `distribution_pool` and pushes funds to each one via `aptos_account::deposit_coins` in the same atomic transaction. If the push-payment to *any single* recipient aborts, the whole transaction reverts, and since Move transactions are all-or-nothing, no recipient — including the operator and every well-behaved staker — receives their unlocked funds. This mirrors the reported push-refund DoS pattern (one bad recipient blocking payout to everyone), except here it hits stake/commission payouts instead of NFT-bid refunds.

### Finding Description
`distribute_internal` [1](#0-0)  withdraws the pool's entire withdrawable balance up front (`stake::withdraw_with_cap`), buys every shareholder into `distribution_pool` shares, and then iterates:

```
while (distribution_pool.shareholders_count() > 0) {
    ...
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute))
}
``` [2](#0-1) 

`aptos_account::deposit_coins` only auto-registers a coin store for `to` if `can_receive_direct_coin_transfers(to)` is true; otherwise it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`:
```
if (!coin::is_account_registered<CoinType>(to)) {
    assert!(can_receive_direct_coin_transfers(to), error::permission_denied(...));
    coin::register<CoinType>(&create_signer(to));
};
coin::deposit<CoinType>(to, coins)
``` [3](#0-2) 

`can_receive_direct_coin_transfers` reads the `DirectTransferConfig.allow_arbitrary_coin_transfers` flag, which is fully self-controlled by the account owner via the public entry function `set_allow_direct_coin_transfers` [4](#0-3) . Any unprivileged staker (delegator on a `staking_contract`) can therefore:
1. Add stake through `staking_contract::create_staking_contract`/`add_stake` to become a distribution-pool shareholder.
2. Never register a `CoinStore<AptosCoin>` for a *separate* address they control that is also entered as a shareholder (or simply call `set_allow_direct_coin_transfers(false)` on their own account and remain unregistered), so `coin::is_account_registered` is false and `can_receive_direct_coin_transfers` is false.
3. Trigger `distribute` / `request_commission` / `unlock_stake` / `switch_operator`, all of which call `distribute_internal` [5](#0-4) [6](#0-5) [7](#0-6) .

Because the loop pays every shareholder from the same withdrawn `coins` object in one transaction, the abort on the attacker's payout reverts the entire transaction: the operator's commission, and every other staker's unlocked principal/rewards, fail to be delivered, even though the stake had already become inactive/withdrawable on the underlying stake pool.

### Impact Explanation
This breaks the "Accounting across active/pending_active/pending_inactive/inactive/rewards/commission state must preserve value and withdrawal rights" invariant: legitimate stakers and the operator lose the ability to withdraw already-unlocked funds as long as one uncooperative shareholder remains in `distribution_pool`. Because `distribute_internal` is also invoked implicitly from `request_commission`, `unlock_stake`, and `switch_operator`, this DoS can block the operator's commission collection and any staker's future unlocks/operator switches on that staking contract, not merely the explicit `distribute()` entrypoint. This qualifies as a high-impact stake-flow DoS: value is not stolen, but withdrawal rights of *other* parties are non-recoverably blocked until the attacker is coincidentally removed from `distribution_pool` (which itself only happens via a successful distribution — a circular dependency).

### Likelihood Explanation
Likelihood is moderate-to-high: setting `allow_arbitrary_coin_transfers = false` and remaining unregistered for `AptosCoin` is a normal, unprivileged, and free action any account can take at any time via `set_allow_direct_coin_transfers`. No special role or governance permission is needed — only becoming a shareholder in the target `staking_contract` (e.g., by staking via that operator, in the pooled `staking_contract`/`vesting` flow) is required.

### Recommendation
In `distribute_internal`, do not let one recipient's failed push abort the whole distribution: wrap each `aptos_account::deposit_coins` call so failures are isolated per-recipient (e.g., check `coin::is_account_registered` / `can_receive_direct_coin_transfers` beforehand and, if the recipient cannot accept the transfer, retain their share/coins in a claimable pending-distribution bucket instead of aborting), continuing to pay out all other shareholders. Alternatively, force each recipient to be registered for `AptosCoin` before allowing them to be added to `distribution_pool`, or split the batch payout into a pull-based per-recipient claim function so a single non-cooperative party cannot block others.

### Proof of Concept
1. Staker `S` sets up `staking_contract::create_staking_contract(S, operator=O, ...)` and stake accrues rewards, or a delegator `D` (with `D` not registered for `AptosCoin` and `D` having called `aptos_account::set_allow_direct_coin_transfers(D, false)`) becomes part of the same distribution flow (e.g., via `unlock_stake` creating a distribution entry for `D`).
2. Time passes; stake becomes `inactive`/`pending_inactive` on the underlying stake pool, and `distribution_pool` now contains entries for both the operator `O` (commission) and `D`.
3. Anyone calls `staking_contract::distribute(S, O)` (or triggers it via `request_commission`/`unlock_stake`/`switch_operator`).
4. `distribute_internal` withdraws all inactive stake, then in the payout loop reaches `D`'s entry and calls `aptos_account::deposit_coins(D, ...)`; since `D` is unregistered and `allow_arbitrary_coin_transfers == false`, this aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire `distribute` transaction reverts — the operator `O` never receives commission and no other shareholder receives their unlocked stake, even though funds were already unlocked at the stake-pool level. This condition persists on every subsequent retry as long as `D`'s registration/opt-out state is unchanged.

Note: I was not able to fully verify within available context whether `coin::is_account_registered<AptosCoin>` (post fungible-asset migration) still gates on the legacy `CoinStore` existence or has been fully replaced by primary-fungible-store semantics that might auto-create the store unconditionally; the `coin.move` source needed to confirm `is_account_registered`/`deposit` behavior for `AptosCoin` specifically was truncated in the tool output, so this part of the causal chain relies on the interface contract shown in `aptos_account.move` rather than a directly-confirmed `coin.move` body. A Devin session with full file access should verify `coin::is_account_registered<AptosCoin>` and `coin::deposit<AptosCoin>` to confirm the abort path is still reachable post-FA migration before treating this as fully proven.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L624-635)
```text
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );

        request_commission_internal(
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L784-789)
```text
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L856-900)
```text
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
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L34-48)
```text
    struct DirectTransferConfig has key {
        allow_arbitrary_coin_transfers: bool,
        update_coin_transfer_events: EventHandle<DirectCoinTransferConfigUpdatedEvent>
    }

    /// Event emitted when an account's direct coins transfer config is updated.
    struct DirectCoinTransferConfigUpdatedEvent has drop, store {
        new_allow_direct_transfers: bool
    }

    #[event]
    struct DirectCoinTransferConfigUpdated has drop, store {
        account: address,
        new_allow_direct_transfers: bool
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L123-131)
```text
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
