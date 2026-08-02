Found it. `deposit_coins` in `aptos_account.move` calls `can_receive_direct_coin_transfers(to)`, which any unprivileged account can set to `false` via `set_allow_direct_coin_transfers`, and this is checked unconditionally for any `CoinType` the recipient hasn't registered for yet — no special "blacklist" capability is required. [1](#0-0) [2](#0-1) 

### Title
Griefing-blocked staking_contract commission/stake distribution loop when a recipient opts out of direct coin transfers - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::distribute_internal` iterates over **all** distribution-pool shareholders in a single transaction and calls `aptos_account::deposit_coins` for each. `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has never registered a `CoinStore<CoinType>` and has explicitly opted out of unsolicited transfers via `set_allow_direct_coin_transfers(false)`. Since staking-contract distributions pay out in `AptosCoin`/whatever `CoinType` is used to a set of stakers/operator addresses that were added over time (e.g., after `switch_operator`, or multiple past stakers who never claimed), any single one of those recipients can unregister/opt out and permanently break the shared `while` loop, exactly mirroring the sherlock report's "one bad recipient blocks batch settlement for everyone" pattern.

### Finding Description
`distribute_internal` withdraws the pool's total inactive/pending_inactive stake into a single `Coin<AptosCoin>` and loops: [3](#0-2) 

For each shareholder it calls `aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute))`. Inside `deposit_coins`, if the recipient's `CoinStore<CoinType>` doesn't already exist, the function requires `can_receive_direct_coin_transfers(to)` to be true, else it aborts: [1](#0-0) 

Any account can flip this flag at will, at zero cost, with a plain unprivileged `set_allow_direct_coin_transfers(account, false)` call — no admin or freeze capability required: [4](#0-3) 

Because `distribute_internal` is a single atomic transaction that must successfully pay every shareholder in the distribution pool before returning, if *any* shareholder address in that loop (1) has never registered `CoinStore<AptosCoin>` (a very common state for a staker/operator that just changed operators or has never held APT directly) and (2) has set `allow_arbitrary_coin_transfers = false`, the whole transaction aborts. Since `distribute`, `request_commission`, `unlock_stake`, and `switch_operator` all call `distribute_internal` first (to flush any already-inactive funds) before doing their own logic, this one griefing condition blocks:
- the staker's own ability to withdraw/unlock further stake,
- the operator's ability to receive commission,
- and anyone's ability to distribute already-unlocked/inactive stake for that pool at all,

for as long as that one recipient keeps the opt-out flag set — this is essentially permanent since it's fully attacker-controlled and cheap to maintain (or even simply cheaper to trigger: any account that has literally never touched `AptosCoin`/the relevant `CoinType` defaults to "did not register", the difference is only in the opt-out flag).

The identical structural bug (and directly analogous to the vesting.move `distribute`/`distribute_many` and `delegation_pool` commission buy-in flows) also exists because `distribute` is `public entry fun distribute(staker: address, operator: address)` — callable by anyone, but the *ability to grief it* rests entirely with whichever address is a current or historical shareholder in `staking_contract.distribution_pool`.

### Impact Explanation
This blocks fund distribution/withdrawal for **all other parties** sharing that staking contract (staker and operator alike), not just the griefing party's own funds — matching the "Stake And Lockup Gate" criterion of loss/strand of stake, commission, or withdrawal rights for accounts other than the attacker. Because `unlock_stake` and `request_commission` both force a `distribute_internal` first, a griefer that is (or once was) a shareholder can permanently prevent the staker from unlocking additional stake and the operator from ever collecting commission on that pool, trapping value with no recovery path in the affected module. This satisfies the "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" impact bucket.

### Likelihood Explanation
Likelihood is high: no privileged capability is needed, `set_allow_direct_coin_transfers` is a completely permissionless entry function, and the condition ("recipient has an existing distribution-pool entitlement but no registered CoinStore") arises naturally whenever an operator/staker relationship changes (e.g., after `switch_operator`, when a former operator's address remains a shareholder in old distributions, or in general use where users have never manually registered a `CoinStore<AptosCoin>` because they always relied on auto-registration via `aptos_account::transfer`). Any actor who anticipates being included as a shareholder (e.g., an operator who is about to be paid commission but disputes the terms, or a griefer front-running their own removal) can proactively call `set_allow_direct_coin_transfers(false)` from their own account to weaponize this.

### Recommendation
In `staking_contract::distribute_internal` (and the structurally identical `vesting::distribute`/`distribute_many`), don't let one recipient's failed deposit abort the whole distribution loop. Options: (1) wrap each per-recipient payout in a way that on failure re-buys shares for that recipient (skip-and-retain) instead of atomically failing the whole loop, or (2) use `coin::deposit`-style handling that falls back to holding undistributed funds in an escrow/claimable balance for that specific shareholder rather than requiring `deposit_coins` to succeed for every shareholder in the same transaction.

### Proof of Concept
1. Staker creates a staking contract with an operator; both accounts, plus a delegator/second recipient `X`, eventually become shareholders in `staking_contract.distribution_pool` (e.g., via `add_distribution` on `unlock_stake` or `switch_operator`).
2. `X` never registers `CoinStore<AptosCoin>` explicitly (i.e., always relied on auto-registration) and calls `aptos_account::set_allow_direct_coin_transfers(X, false)`.
3. Stake pool lockup expires (`stake::fast_forward_to_unlock`), making `X`'s owed distribution withdrawable/inactive.
4. Anyone calls `staking_contract::distribute(staker, operator)` (or the staker calls `unlock_stake`/the operator calls `request_commission`). `distribute_internal`'s loop reaches `X`, calls `aptos_account::deposit_coins(X, ...)`, which asserts `can_receive_direct_coin_transfers(X)` → `false` → aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire transaction reverts: the operator cannot collect commission, the staker cannot unlock further stake, and no other shareholder in that pool can be paid, until `X` re-enables direct transfers (which `X` fully controls and has no incentive to do).

*Note: I was not able to fully trace every historical code path that populates `distribution_pool` shareholders (e.g., all scenarios where a former operator/staker address persists as a shareholder after `switch_operator`) within the available index; a Devin session with full repo/test access would be needed to confirm the exact set of realistic pre-conditions and write an executable Move unit test reproducing the abort end-to-end.*

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-231)
```text
    /// Set whether `account` can receive direct transfers of coins that they have not explicitly registered to receive.
    public entry fun set_allow_direct_coin_transfers(
        account: &signer, allow: bool
    ) acquires DirectTransferConfig {
        let addr = signer::address_of(account);
        if (exists<DirectTransferConfig>(addr)) {
            let direct_transfer_config = borrow_global_mut<DirectTransferConfig>(addr);
            // Short-circuit to avoid emitting an event if direct transfer config is not changing.
            if (direct_transfer_config.allow_arbitrary_coin_transfers == allow) { return };

            direct_transfer_config.allow_arbitrary_coin_transfers = allow;

            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
        } else {
            let direct_transfer_config = DirectTransferConfig {
                allow_arbitrary_coin_transfers: allow,
                update_coin_transfer_events: new_event_handle<
                    DirectCoinTransferConfigUpdatedEvent>(account)
            };
            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
            move_to(account, direct_transfer_config);
        };
    }

    #[view]
    /// Return true if `account` can receive direct transfers of coins that they have not explicitly registered to
    /// receive.
    ///
    /// By default, this returns true if an account has not explicitly set whether the can receive direct transfers.
    public fun can_receive_direct_coin_transfers(
        account: address
    ): bool acquires DirectTransferConfig {
        !exists<DirectTransferConfig>(account)
            || borrow_global<DirectTransferConfig>(account).allow_arbitrary_coin_transfers
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
