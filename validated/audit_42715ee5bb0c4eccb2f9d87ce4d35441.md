## Analog Found: Distribution DoS via Unregistered/Direct-Transfer-Disabled Beneficiary in `staking_contract::distribute_internal`

### Title
Operator can permanently block `staking_contract` fund distribution (staker principal, rewards, and commission) by routing commission to a beneficiary that rejects direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` pays out every shareholder in a shared `distribution_pool` in a single loop, using `aptos_account::deposit_coins`. If any single recipient in that loop cannot receive the deposit, the entire transaction — and thus payouts to *all other* shareholders including the staker — aborts. An operator can weaponize this by pointing their `beneficiary_for_operator` at an address that has disabled direct/unregistered coin transfers, permanently DoS-ing `distribute`, `request_commission`, `unlock_stake`, `switch_operator`, and `update_commission_percentage` for the whole staking contract. This mirrors the SEDA "batch sender reverts on receiving fee" pattern: one untrusted payout recipient in a shared distribution loop can block funds for everyone else.

### Finding Description
`distribute_internal` iterates all shareholders of the internal `pool_u64` distribution pool and pays each one via `aptos_account::deposit_coins`, substituting the operator's beneficiary for the operator itself: [1](#0-0) 

`aptos_account::deposit_coins` only succeeds for an unregistered recipient if that account has *not* opted out of direct transfers: [2](#0-1) 

Any account holder can permissionlessly disable direct coin transfers for their own address via `set_allow_direct_coin_transfers(account, false)`: [3](#0-2) 

An operator can freely repoint their beneficiary to any address at any time via `set_beneficiary_for_operator`: [4](#0-3) 

Attack sequence:
1. Operator controls (or colludes with) address `B`, which has never registered a CoinStore for `AptosCoin` and has called `aptos_account::set_allow_direct_coin_transfers(false)`.
2. Operator calls `staking_contract::set_beneficiary_for_operator(operator, B)`.
3. Any subsequent call that reaches `distribute_internal` — `distribute`, `request_commission`, `unlock_stake`, `switch_operator`, `update_commission_percentage` — will, once it tries to pay `B` its commission share, hit `aptos_account::deposit_coins(B, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
4. Because the payout loop is a single atomic transaction, this abort reverts the whole call, meaning **the staker's own principal/rewards distribution (paid in the same loop, or gated by the same `distribute_internal` call in `unlock_stake`/`switch_operator`) is also blocked**, not just the operator's own commission.

The same class of risk exists in `vesting.move`, whose `distribute`/`terminate_vesting_contract`/`admin_withdraw` flows call into `staking_contract::distribute` and inherit this same shared-loop, single-revert design: [5](#0-4) 

### Impact Explanation
This breaks the stake/lockup invariant that "unlock, reactivate, withdraw ... paths must not redirect value or strand it permanently." A staker's ability to withdraw principal via `unlock_stake` (which internally forces `distribute_internal`) or to `switch_operator` away from a hostile operator is permanently blocked, since `switch_operator` itself calls `distribute_internal` before performing the switch: [6](#0-5) 

This effectively strands legitimate value (staker principal + accrued rewards) with no on-chain recovery path other than the malicious beneficiary voluntarily re-enabling direct transfers — a non-recoverable-loss-of-claim scenario matching the required impact class.

### Likelihood Explanation
The only prerequisite is holding (or having been granted) the operator role for a `staking_contract`/vesting pool and controlling one address that never registers `AptosCoin` and calls a single permissionless, free entry function (`set_allow_direct_coin_transfers`). No collusion with the staker or governance is required, and the griefing is essentially free (one transaction to disable transfers, one to set the beneficiary).

### Recommendation
- In `distribute_internal`, isolate each shareholder payout so a single failing recipient does not revert payouts to the others (e.g., wrap each `aptos_account::deposit_coins` call so failures fall back to crediting an internal claimable balance instead of aborting the whole loop).
- Alternatively, validate at `set_beneficiary_for_operator` time (and periodically) that the beneficiary can currently receive `AptosCoin`, or disallow beneficiaries that have `allow_arbitrary_coin_transfers == false` and are unregistered.
- Consider decoupling commission "unlock" bookkeeping from the actual token transfer so a payout failure only affects the specific unpaid recipient's claim, not the entire distribution transaction.

### Proof of Concept
1. Staker creates a staking contract with `operator` O (`staking_contract::create_staking_contract`).
2. O's colluding/controlled account `B` calls `aptos_account::set_allow_direct_coin_transfers(B, false)` and never registers `AptosCoin`.
3. O calls `staking_contract::set_beneficiary_for_operator(O, B)`.
4. Stake pool earns rewards; staker calls `staking_contract::unlock_stake(staker, O, amount)` (or `distribute`, or attempts `switch_operator` to remove O).
5. `distribute_internal` reaches the shareholder entry for O, substitutes recipient `B`, calls `aptos_account::deposit_coins(B, ...)`, which hits the `!coin::is_account_registered` branch, evaluates `can_receive_direct_coin_transfers(B) == false`, and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the entire transaction and leaving the staker's stake permanently un-withdrawable through this path.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L784-796)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-130)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-756)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);






























                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount: total_distribution_amount,
            },
        );
    }
```
