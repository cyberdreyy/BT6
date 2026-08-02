## Analysis

The Renzo bug's core invariant is: **a single unprivileged recipient must not be able to permanently block or strand assets belonging to other, uninvolved parties in a shared withdrawal/distribution flow.** In Renzo this happened because `claim()` used gas-limited `transfer()`; in Aptos there is no gas-forwarding issue, but an analogous "recipient can make itself un-payable" primitive exists via `aptos_account::set_allow_direct_coin_transfers`.

### Candidate paths considered
1. `delegation_pool` commission distribution to `beneficiary_for_operator` — uses `buy_in_active_shares`/`buy_in_pending_inactive_shares` (accounting-only, no direct coin deposit), so it cannot revert on recipient opt-out. Ruled out.
2. `stake::withdraw`/`withdraw_with_cap` — deposits only to the caller's own account. No cross-user lockup. Ruled out.
3. `staking_contract::distribute_internal` and `vesting::distribute` — both iterate over **all** shareholders/recipients of a shared pool in a single atomic transaction and call `aptos_account::deposit_coins` for each recipient. **Kept as strongest candidate.**

### Root cause
`aptos_account::deposit_coins` (the same logic path shown for `transfer_coins`/`batch_transfer` in [1](#0-0) ) checks `coin::is_account_registered<CoinType>(to)`, and if unregistered, requires `can_receive_direct_coin_transfers(to)` to be true or it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`. Any account can unprivilegedly disable this via `set_allow_direct_coin_transfers(account, false)` [2](#0-1) .

Both `staking_contract::distribute_internal` (loop over `distribution_pool.shareholders()`, calling `aptos_account::deposit_coins` per recipient) [3](#0-2)  and `vesting::distribute` (loop over `grant_pool.shareholders()`, calling `aptos_account::deposit_coins` per shareholder) [4](#0-3)  process **multiple, unrelated parties' payouts inside one atomic Move transaction**. Since Move aborts are atomic (no partial commits), if the deposit to *any single* recipient in that loop aborts, the whole `distribute()` call reverts — for every other shareholder in the same pool, not just the offending one.

`distribute_internal` is also invoked as a prerequisite step inside `unlock_stake`, `request_commission`, `switch_operator`, and `update_commission_percentage` [5](#0-4) , so the same recipient-triggerable abort also blocks those other unprivileged, legitimate staker/operator actions.

### Impact
Because a shareholder/operator-beneficiary set is a shared, permissionless data structure (any staker or vesting admin can add arbitrary shareholder addresses when the contract is created, and a beneficiary/operator can be swapped in by other flows), one address that has never registered `AptosCoin`/opted out of direct transfers permanently blocks `distribute()`, and transitively `unlock_stake`, `request_commission`, `switch_operator`, and `update_commission_percentage` for **all** other shareholders/stakers of that pool, until/unless that one account registers `AptosCoin` (which it may never choose to do, deliberately or by abandonment). This is a denial-of-distribution that strands other legitimate parties' unlocked stake, commission, and vested rewards indefinitely — matching the "permanent lock … of claim rights in stake, delegation, commission, beneficiary, or vesting flows" impact category.

### Caveat / what I could not fully verify
I could not directly view the body of the specific `aptos_account::deposit_coins` function signature (as opposed to the neighboring `transfer_coins`/`batch_transfer` code with matching logic) within the tool budget available, so the exact abort condition is inferred from the adjacent code block and the `aptos_account.spec.move` documentation (`RegistCoinAbortsIf`, "deposit_coins function verifies if the recipient account exists…and ensures registration") [6](#0-5)  rather than a byte-for-byte read of `deposit_coins` itself. I recommend a Devin session with full file access to confirm the exact function body of `aptos_account::deposit_coins` before treating this as conclusively proven, and to check whether under the current fungible-asset migration AptosCoin transfers actually bypass this registration/opt-out check entirely (in which case the finding would not hold for `AptosCoin` specifically, only for non-migrated coin types).

### Title
Single opted-out shareholder can permanently block reward/commission distribution for all other stakers in a `staking_contract`/`vesting` pool — (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`, `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Recommendation
In `distribute_internal` and `vesting::distribute`, wrap each per-recipient `aptos_account::deposit_coins` call so that a failure for one recipient does not abort the whole transaction (e.g., catch failures by using a lower-level deposit that always succeeds once the coin store exists, or explicitly force-create/register the recipient's `CoinStore`/primary store as part of pool onboarding, independent of `can_receive_direct_coin_transfers`, since these are pre-committed distribution obligations rather than arbitrary/unsolicited transfers).

Given the uncertainty flagged above, this should be validated with full repository access (a Devin session) before remediation, specifically to confirm the current body of `aptos_account::deposit_coins` for `AptosCoin` under the fungible-asset migration.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L123-130)
```text
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-219)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L582-630)
```text
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
        let old_commission_percentage = staking_contract.commission_percentage;
        staking_contract.commission_percentage = new_commission_percentage;
        emit(
            UpdateCommission {
                staker: staker_address,
                operator,
                old_commission_percentage,
                new_commission_percentage
            }
        );
    }

    /// Unlock commission amount from the stake pool. Operator needs to wait for the amount to become withdrawable
    /// at the end of the stake pool's lockup period before they can actually can withdraw_commission.
    ///
    /// Only staker, operator or beneficiary can call this.
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-747)
```text
        // Distribute coins to all shareholders in the vesting contract.
        let grant_pool = &vesting_contract.grant_pool;
        let shareholders = &grant_pool.shareholders();
        shareholders.for_each_ref(|shareholder| {
            let shareholder = *shareholder;
            let shares = pool_u64::shares(grant_pool, shareholder);
            let amount = pool_u64::shares_to_amount_with_total_coins(grant_pool, shares, total_distribution_amount);
            let share_of_coins = coin::extract(&mut coins, amount);
            let recipient_address = get_beneficiary(vesting_contract, shareholder);
            aptos_account::deposit_coins(recipient_address, share_of_coins);
        });

        // Send any remaining "dust" (leftover due to rounding error) to the withdrawal address.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(vesting_contract.withdrawal_address, coins);
        } else {
            coin::destroy_zero(coins);
        };
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.spec.move (L43-49)
```text
    /// No.: 6
    /// Requirement: Creating an account for the provided destination and registering it for that particular CoinType
    /// should be the only way to enable depositing coins, provided the account does not already exist.
    /// Criticality: Critical
    /// Implementation: The deposit_coins function verifies if the recipient account exists. If the account does not
    /// exist, the function creates one and ensures that the account becomes registered for the specified CointType.
    /// Enforcement: Formally verified via [high-level-req-6](deposit_coins).
```
