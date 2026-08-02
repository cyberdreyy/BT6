## Title
Griefing lock of stake/commission/vesting distributions via `set_allow_direct_coin_transfers(false)` on a single recipient — (`aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` loops over **every** shareholder in the staking contract's `distribution_pool` and pays each one with `aptos_account::deposit_coins` inside a single atomic loop, with no ability to skip a failing recipient. `aptos_account::deposit_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is not registered for `AptosCoin` and has explicitly disabled direct coin transfers via `aptos_account::set_allow_direct_coin_transfers(false)`. Because this call is unprivileged and can be issued by any staker/beneficiary participating in a `staking_contract` (or a vesting contract built on top of it), a single uncooperative shareholder can permanently abort `distribute()` for the whole staking contract, blocking commission and stake-withdrawal payouts to every other staker/operator sharing that pool. `vesting.move`'s `withdraw_stake`/`admin_withdraw`/`distribute` paths call the same `staking_contract::distribute`, so the same griefing also permanently blocks vesting payouts and admin withdrawal after contract termination.

### Finding Description
`aptos_account::deposit_coins` conditionally requires the recipient to accept unregistered direct transfers: [1](#0-0) 

The permission flag is user-controlled and unprivileged: [2](#0-1) 

`staking_contract::distribute_internal` pays out every shareholder of the `distribution_pool` (stakers, and the operator/beneficiary) in one `while` loop, aborting the whole function (and thus the whole transaction, with all prior loop iterations rolled back) if any single `deposit_coins` call fails: [3](#0-2) 

Because Move transactions are atomic, an abort partway through the `while` loop reverts every payment already processed in that call — not just the offending recipient's payment. Since the `distribution_pool` still holds the withdrawn `Coin<AptosCoin>` shares/values for the unprocessed recipients (nothing was persisted because the transaction aborted), the entire distribute() call becomes permanently un-callable as long as the blocking recipient remains both (a) unregistered for `AptosCoin`, and (b) has `allow_arbitrary_coin_transfers = false`. Both conditions are fully within that recipient's own unprivileged control via `set_allow_direct_coin_transfers`.

This mechanism is the direct Aptos-native analog of the Solidity `payable.transfer()` issue in the external report: a recipient that cannot successfully receive funds (there, due to gas-limited fallback; here, due to opted-out direct transfers) blocks a shared payout flow that also serves *other, unrelated* users, resulting in their funds becoming stuck/non-withdrawable.

The same `staking_contract::distribute` function is invoked from `vesting::withdraw_stake`, which underlies `vesting::distribute` (periodic shareholder rewards/vesting payout) and `vesting::admin_withdraw` (final admin recovery of a terminated vesting contract): [4](#0-3) [5](#0-4) 

Any shareholder in the vesting contract's distribution pool, or the operator itself, can trigger the identical block: once they toggle off direct transfers and remain unregistered for `AptosCoin`, `distribute()` reverts every time it is called, freezing rewards for all other shareholders as well as the admin's ability to reclaim the vesting contract's residual funds via `admin_withdraw`.

### Impact Explanation
This breaks the "Unlock, reactivate, withdraw, synchronize, and beneficiary-update paths must not redirect value or strand it permanently" invariant. A single unprivileged shareholder (staker or vesting shareholder) sharing a `StakingContract`/`VestingContract` with others can, without any special role, permanently strand:
- Other stakers' unlocked/withdrawable stake in the same `staking_contract`.
- The operator's/beneficiary's accrued commission in the same `staking_contract`.
- Other vesting shareholders' vested/reward distributions in the same `VestingContract`.
- The admin's ability to recover residual funds via `admin_withdraw` after contract termination.

Funds are not stolen or redirected, but they become permanently non-withdrawable through the intended distribution paths for parties who do not control the blocking account — this matches the "Permanent lock or non-recoverable loss of claim rights" impact class.

### Likelihood Explanation
Any account that is (or can become) a shareholder in a shared `staking_contract`/vesting contract — e.g. by simply being added as a vesting shareholder, or a staker joining an existing pool — can call the public `aptos_account::set_allow_direct_coin_transfers(false)` (no special privilege required) and simply not register for `AptosCoin`. Since staking contracts commonly host multiple stakers/shareholders sharing one `distribution_pool`, the precondition is realistic in normal usage rather than requiring an exotic setup. The main uncertainty is whether most participating accounts are already registered for `AptosCoin` by default (in which case `deposit_coins` skips the `can_receive_direct_coin_transfers` check entirely) — this reduces exploitability to accounts that have not yet interacted with AptosCoin/`aptos_account`, which is plausible for freshly-created vesting-contract shareholder accounts before they ever receive funds directly.

### Recommendation
Make `distribute_internal`/`vesting::distribute` resilient to individual payout failures: e.g., wrap each recipient's `deposit_coins` so a failure for one recipient does not revert the whole loop (skip and retain that recipient's share in the pool for later retry), or force `coin::register`/require prior registration independent of the opt-out flag for pool-shared payout flows, or process payouts individually per-recipient (separate transactions/entry points) rather than in one atomic all-or-nothing loop.

### Proof of Concept
1. Staker `S1` and staker `S2` (or vesting shareholder `V2`) both hold shares in the same `staking_contract`/vesting contract with operator `O`.
2. `S2` calls `aptos_account::set_allow_direct_coin_transfers(S2, false)` and never registers a `CoinStore`/PFS for `AptosCoin` directly (i.e., relies only on being auto-registered elsewhere, or is a freshly created account added straight as a vesting shareholder without ever transacting AptosCoin).
3. Rewards accrue and unlock; anyone calls `staking_contract::distribute(staker_address, operator_address)` (directly, or transitively via `vesting::distribute`/`vesting::admin_withdraw`).
4. `distribute_internal`'s loop reaches `S2`'s entry in the `distribution_pool` and calls `aptos_account::deposit_coins(S2, ...)`, which asserts `can_receive_direct_coin_transfers(S2)` — false — and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire transaction reverts, including the payouts already computed for `S1`/operator in earlier loop iterations. `S1`'s stake withdrawal and the operator's commission remain permanently unreachable through `distribute()` as long as `S2`'s configuration persists, since there is no code path to skip or isolate `S2`'s failing payout.

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L226-231)
```text
    public fun can_receive_direct_coin_transfers(
        account: address
    ): bool acquires DirectTransferConfig {
        !exists<DirectTransferConfig>(account)
            || borrow_global<DirectTransferConfig>(account).allow_arbitrary_coin_transfers
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-920)
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

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L795-821)
```text
    /// Withdraw all funds to the preset vesting contract's withdrawal address. This can only be called if the contract
    /// has already been terminated.
    public entry fun admin_withdraw(admin: &signer, contract_address: address) acquires VestingContract {
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        assert!(
            vesting_contract.state == VESTING_POOL_TERMINATED,
            error::invalid_state(EVESTING_CONTRACT_STILL_ACTIVE)
        );

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let amount = coin::value(&coins);
        if (amount == 0) {
            coin::destroy_zero(coins);
            return
        };
        aptos_account::deposit_coins(vesting_contract.withdrawal_address, coins);

        emit(
            AdminWithdraw {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1071-1078)
```text
    fun withdraw_stake(vesting_contract: &VestingContract, contract_address: address): Coin<AptosCoin> {
        // Claim any withdrawable distribution from the staking contract. The withdrawn coins will be sent directly to
        // the vesting contract's account.
        staking_contract::distribute(contract_address, vesting_contract.staking.operator);
        let withdrawn_coins = coin::balance<AptosCoin>(contract_address);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        coin::withdraw<AptosCoin>(contract_signer, withdrawn_coins)
    }
```
