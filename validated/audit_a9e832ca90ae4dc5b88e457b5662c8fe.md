### Title
Unregistered/opted-out beneficiary in `staking_contract::set_beneficiary_for_operator` permanently DoS's `distribute_internal`, freezing all staker funds - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` when the recipient has opted out of unregistered direct coin transfers via `set_allow_direct_coin_transfers(false)` [1](#0-0) . `staking_contract::set_beneficiary_for_operator` lets an operator set any address as beneficiary with **no registration/acceptance check** [2](#0-1) , unlike the analogous `vesting::set_beneficiary`, which explicitly calls `assert_account_is_registered_for_apt(new_beneficiary)` before allowing the change [3](#0-2) . This missing guard reproduces the exact bug class from the external report: the payout function is not resilient to a non-standard/refusing recipient and reverts unconditionally, blocking payout for the entire batch and stranding funds.

### Finding Description
`distribute_internal` iterates over every shareholder in the pending distribution pool (staker + operator/beneficiary) in a single atomic loop and calls `aptos_account::deposit_coins` for each recipient [4](#0-3) . If any single recipient in that loop cannot accept the deposit, the whole call — and therefore the whole enclosing transaction — reverts, since Move transactions are atomic.

`deposit_coins` can revert for an address that is not registered for the coin AND has disabled arbitrary/direct coin transfers, per `can_receive_direct_coin_transfers` [5](#0-4) .

Critically, `distribute_internal` is not only invoked from the standalone `distribute()` entry function — it is invoked as the *first step* of nearly every staking_contract mutating entry function: `unlock_stake`, `request_commission`, `update_commision`, and `switch_operator` all call `distribute_internal` before doing anything else [6](#0-5) [7](#0-6) [8](#0-7) .

Because `set_beneficiary_for_operator` never validates that `new_beneficiary` can actually receive APT, an operator can:
1. Cause the designated beneficiary account to opt out of direct transfers (e.g., by calling `set_allow_direct_coin_transfers(false)` on that account, or simply pointing to any account that is not registered for `AptosCoin` and has opted out), and
2. Call `set_beneficiary_for_operator(operator, poisoned_beneficiary)`.

From that point on, any call to `distribute_internal` for that staker/operator pair will attempt `aptos_account::deposit_coins(poisoned_beneficiary, ...)` for the operator's commission share and abort. Since `distribute_internal` runs first inside `unlock_stake`, `request_commission`, `update_commision`, and `switch_operator`, the abort propagates and blocks the staker from unlocking stake, requesting withdrawal, changing commission, or even switching away from the malicious operator.

### Impact Explanation
This traps the staker's principal and any accrued rewards inside the staking contract indefinitely: every unlock/withdraw/switch-operator path depends on `distribute_internal` succeeding first, and it cannot succeed while the poisoned beneficiary remains unable to receive the deposit. Because the staker has no way to change `operator`'s beneficiary (that control belongs solely to the operator per `set_beneficiary_for_operator`'s `operator: &signer` parameter), the staker's only recourse is to hope the operator or its beneficiary re-enables `allow_direct_coin_transfers` — something the attacker (operator) explicitly controls and can refuse to do. This matches "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows," and additionally corrupts share accounting behavior (`total_potential_withdrawable` funds get pulled from the stake pool via `stake::withdraw_with_cap` inside the same aborted transaction, but since Move transactions are atomic, this state change also reverts, so no funds are physically lost — the impact is a hard functional lock, not fund destruction).

### Likelihood Explanation
Likelihood is high for a malicious or compromised operator: `set_beneficiary_for_operator` is a standard operator-privileged entry function gated only by the `operator_beneficiary_change_enabled()` feature flag, with no check that the address is registered/willing to receive APT. An operator who wants to grief a staker (e.g., to force renegotiation of terms, hold funds hostage, or retaliate after a dispute) needs only two ordinary transactions (disable direct transfers on a beneficiary account they control, then call `set_beneficiary_for_operator`).

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator`: require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or equivalently verify `coin::is_account_registered<AptosCoin>` or `can_receive_direct_coin_transfers`) before accepting the new beneficiary. Additionally, consider hardening `distribute_internal` itself so a single non-receiving recipient cannot block payouts to the rest of the shareholders — e.g., skip/queue the failing recipient's redemption rather than aborting the whole loop, mirroring the SafeERC20-style isolation recommended in the original report.

### Proof of Concept
1. Staker creates a staking contract with `operator` via `staking_contract::create_staking_contract`.
2. `operator` creates/controls address `B` and calls `aptos_account::set_allow_direct_coin_transfers(&B_signer, false)` (opts B out of receiving un-registered coin deposits), and ensures `B` is not registered for `AptosCoin`.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, B)` — succeeds because no registration check exists [2](#0-1) .
4. Stake pool accrues rewards; `operator` calls `request_commission`, which internally calls `distribute_internal` first [9](#0-8) . Once there is a non-zero amount pending in the distribution pool destined for `B` (the operator's beneficiary), any subsequent call to `distribute_internal` will try `aptos_account::deposit_coins(B, ...)` and abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. Staker now calls `unlock_stake(staker, operator, amount)` to withdraw their own principal — this aborts because it calls `distribute_internal` first [10](#0-9) . The staker is permanently unable to unlock or withdraw stake, and cannot use `switch_operator` either, since it also calls `distribute_internal` first [11](#0-10) .

Note: I was not able to execute this scenario in a live Move test environment (no execution tools available in this session); the analysis is based on static tracing of the call graph and function bodies above, which show unconditionally that `distribute_internal` is invoked as a precondition for `unlock_stake`, `request_commission`, `update_commision`, and `switch_operator`, and that it will abort on a non-accepting recipient with no isolation or fallback path.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L109-131)
```text
    /// Convenient function to deposit a custom CoinType into a recipient account that might not exist.
    /// This would create the recipient account first and register it to receive the CoinType, before transferring.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L564-601)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-635)
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

        request_commission_internal(
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L678-719)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L782-805)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-927)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);

```
