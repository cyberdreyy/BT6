## Analysis

The external report's core bug class is: *an untrusted payout recipient can force a shared, all-or-nothing payout operation to fail, blocking value for everyone else in that batch.* In Solidity this manifests as gas-griefing via `.call{value}`; in Move, the same push-based, single-transaction, loop-over-all-recipients pattern manifests as a **permanent abort** that reverts the entire transaction (Move has no ability to `try/catch` a failed sub-call the way Solidity does), making the resulting "denial of service" strictly worse: it doesn't just waste gas, it can strand every other party's already-unlocked funds indefinitely.

`staking_contract::distribute_internal` withdraws **all** currently withdrawable stake in one shot and then loops over every shareholder in the shared `distribution_pool`, calling `aptos_account::deposit_coins` for each one in the same atomic transaction: [1](#0-0) 

If the recipient is the operator, the payout is redirected to whatever address the operator has set as `beneficiary_for_operator`: [2](#0-1) 

That beneficiary address is fully operator-controlled and unconstrained (it need not be pre-registered for AptosCoin, need not even exist yet): [3](#0-2) 

`aptos_account::deposit_coins` aborts if the target account has not registered a `CoinStore<CoinType>` **and** has opted out of unsolicited direct transfers (`DirectTransferConfig.allow_arbitrary_coin_transfers == false`, settable by the account owner at any time): [4](#0-3) 

Because `distribute_internal` pays out **every** shareholder (staker principal/reward withdrawal *and* operator commission) in one shared loop within a single atomic transaction, a single failing recipient aborts the whole call — including the honest staker's own withdrawal that happens to be bundled in the same `distribution_pool` iteration.

### Title
Operator-controlled beneficiary can permanently block staker stake withdrawal via `distribute` abort - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`distribute_internal` pays out all pending shareholders of a staking contract (staker withdrawals and operator commission) in a single atomic loop that calls `aptos_account::deposit_coins` for each recipient. An operator can set their commission beneficiary (`set_beneficiary_for_operator`) to an address that has opted out of unsolicited coin transfers and never registers a `CoinStore<AptosCoin>`. Every subsequent `distribute`, `unlock_stake`, `request_commission`, or `switch_operator` call — all of which invoke `distribute_internal` — will then abort, because `aptos_account::deposit_coins` aborts on that one recipient. Since the withdraw from the stake pool and payout to *all* shareholders happens in the same transaction, this permanently blocks the staker's own principal/reward withdrawal as well, with no way to isolate or skip the poisoned recipient.

### Finding Description
`distribute_internal` withdraws the entire withdrawable balance from the stake pool and then iterates the `distribution_pool` shareholders, using `aptos_account::deposit_coins` to pay each one: [5](#0-4) 

The operator recipient is redirected to `beneficiary_for_operator(operator)`, which is fully controlled by the operator via `set_beneficiary_for_operator` and can be set to any address, without any check that the address is already registered to receive AptosCoin: [6](#0-5) 

`aptos_account::deposit_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is unregistered for `CoinType` and has disabled arbitrary/direct coin transfers: [4](#0-3) 

Because Move transactions are atomic (unlike a Solidity low-level `.call` whose failure can be checked and handled), a single poisoned recipient in the shared payout loop reverts the **entire** `distribute`/`unlock_stake`/`request_commission`/`switch_operator` call, not just that recipient's own share. Every entry point that can trigger a payout funnels through `distribute_internal`: [7](#0-6) [8](#0-7) 

Since `stake::withdraw_with_cap` is called first and unconditionally pulls the entire withdrawable balance out of the stake pool before the loop runs, retrying `distribute` does not help — the same poisoned beneficiary is queued in the pool on every attempt, so the abort recurs deterministically and the staker's unlocked coins remain trapped in the staking-contract resource account with no code path to bypass the bad recipient.

### Impact Explanation
This breaks the "unlock, reactivate, withdraw... must not strand value permanently" invariant for staking_contract. A malicious or uncooperative operator (a role the staker does not control and must trust only for commission-percentage terms, not for withdrawal availability) can, via a purely self-directed configuration action (`set_beneficiary_for_operator` + `aptos_account::set_allow_direct_coin_transfers(false)` on a fresh, unregistered address), permanently prevent the staker from ever completing `distribute()`, `unlock_stake()`, or `switch_operator()` against that staking contract. The staker's already-unlocked stake becomes permanently non-withdrawable as long as the operator's poisoned beneficiary configuration exists. This is a High-severity, unprivileged-operator-triggered lock of another party's stake with no owner override or automatic remediation path.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires an adversarial operator (a counterparty the staker chose, but one who is otherwise not supposed to have withdrawal-blocking power) to deliberately misconfigure their beneficiary. No special privileges beyond the normal operator role are needed, and both `set_beneficiary_for_operator` and `aptos_account::set_allow_direct_coin_transfers` are ordinary unprivileged entry functions.

### Recommendation
Do not let a single failing recipient revert payouts to all other shareholders. Either:
- Isolate each recipient's `deposit_coins` call so a failure for one recipient does not abort the whole `distribute_internal` loop (e.g., catch/skip and re-queue the failed share rather than aborting the transaction), or
- Switch to a pull-based claim model for commission/beneficiary payouts (mirroring the report's recommendation), letting each shareholder withdraw their own share independently instead of a single relayer/caller pushing funds to everyone in one transaction, or
- Validate that a newly set beneficiary is registered for AptosCoin (or force-register it) at the time `set_beneficiary_for_operator` is called, and/or use `coin::register`-style force deposit that cannot be blocked by the recipient's `DirectTransferConfig`.

### Proof of Concept
1. Staker creates a staking contract with `operator` via `create_staking_contract`.
2. `operator` calls `aptos_account::set_allow_direct_coin_transfers(&fresh_signer, false)` on a fresh address `evil_beneficiary` that has never registered `CoinStore<AptosCoin>`.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(&operator, evil_beneficiary)`.
4. Time passes, rewards accrue, staker calls `unlock_stake` (or anyone calls `distribute`).
5. `distribute_internal` withdraws all withdrawable coins from the pool and loops the `distribution_pool`; when it reaches the operator's share, it resolves the recipient to `evil_beneficiary` and calls `aptos_account::deposit_coins(evil_beneficiary, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts — the staker's own share, bundled in the same loop, is never paid out, and every future retry hits the same abort since the withdrawn coins/shares are re-derived identically each time.

I was unable to fully verify, within the available search iterations, whether an analogous unprivileged path exists in `vesting.move`'s `distribute()` (shareholders there are typically added by the vesting `admin`, which the scope treats as a privileged role, so I did not pursue that variant as the primary finding).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-729)
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

        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
        );

        // Request to unlock the distribution amount from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            UnlockStake { pool_address, operator, amount, commission_paid }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-911)
```text
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
