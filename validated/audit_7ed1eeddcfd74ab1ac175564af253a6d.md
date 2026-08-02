## Finding: Missing beneficiary validation in `staking_contract::set_beneficiary_for_operator` permanently locks staker withdrawals

### Title
Unvalidated operator beneficiary in `staking_contract::set_beneficiary_for_operator` can permanently block staker stake withdrawal - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
This is the Move-native analog of the reported "Airlock" bug: an unprivileged party (the operator) can set a payout destination (`new_beneficiary`) that is never validated, and the framework's atomic, all-shareholders-in-one-transaction distribution logic means a single bad recipient permanently blocks payouts to every other party sharing the same distribution pool — including the staker's own already-unlocked principal.

### Finding Description
`staking_contract::set_beneficiary_for_operator` lets any operator redirect their commission payouts to an arbitrary `new_beneficiary` address with zero validation — no check that the address exists, is registered for APT, or accepts direct transfers: [1](#0-0) 

Contrast this with the vesting module's analogous function, `vesting::set_beneficiary`, which explicitly guards against exactly this failure mode by requiring the new beneficiary to be registered for APT before the assignment is allowed: [2](#0-1) 

The consequence of an unvalidated beneficiary shows up in `distribute_internal`, which is the single shared function used to pay out both the operator's commission and the staker's unlocked stake from the same `distribution_pool` in one atomic loop: [3](#0-2) 

If depositing to the operator's beneficiary (`recipient == operator` branch, line 896-898) aborts — e.g., because the beneficiary account has called `aptos_account::set_allow_direct_coin_transfers(false)` — the entire `while` loop aborts, and with it the whole transaction, including the deposit meant for the staker's own withdrawn stake that is processed in the very same loop. [4](#0-3) 

This `distribute_internal` function is called from every stake-movement entry point that an unprivileged staker relies on to get their funds out: `distribute`, `request_commission`, `unlock_stake`, and `switch_operator` all invoke it before doing their own work. [5](#0-4) [6](#0-5) [7](#0-6) 

Because `commission_percentage` need not be zero and the operator's distribution is always processed as part of the same shared pool (`add_distribution`/`update_distribution_pool` on `staking_contract.distribution_pool`), there is no way for the staker to bypass the poisoned beneficiary once inactive stake and commission co-exist in the same pending pool — the transaction is all-or-nothing.

### Impact Explanation
An operator (unprivileged relative to the staker's funds) can unilaterally and permanently strand the staker's already-unlocked stake by:
1. Preparing a beneficiary account that rejects direct coin transfers (`set_allow_direct_coin_transfers(false)`), which is a normal, permissionless self-service action.
2. Calling `set_beneficiary_for_operator` to point at that account — no validation stops this.
3. From then on, any call to `distribute`, `unlock_stake`, `request_commission`, or `switch_operator` on the affected staking contract aborts as soon as there is nonzero pending commission for the operator, because the deposit to the poisoned beneficiary reverts the whole transaction.

This traps the staker's inactive/pending_inactive stake indefinitely (the staker cannot withdraw their own principal), which matches the required impact class of "permanent lock or non-recoverable loss of claim rights in stake ... beneficiary ... flows," directly analogous to the reported airlock being "unable to be cancelled" once `fee_destination` is broken.

### Likelihood Explanation
This requires no elevated privilege — any operator of any staking contract (a role obtainable simply by being designated an operator by a staker, which is a normal, expected relationship, not an attacker-only privilege escalation) can trigger this by calling two already-public entry functions (`set_allow_direct_coin_transfers` and `set_beneficiary_for_operator`). No governance, no admin key, no race condition is needed, making this straightforward and repeatable.

### Recommendation
Validate `new_beneficiary` in `staking_contract::set_beneficiary_for_operator` the same way `vesting::set_beneficiary` already does — require `aptos_account::assert_account_is_registered_for_apt(new_beneficiary)` (or equivalently confirm it can currently receive direct transfers) before accepting the change. Additionally, consider decoupling operator-commission distribution failure from staker distribution in `distribute_internal` so that a single bad recipient cannot revert payouts owed to unrelated shareholders in the same pool (e.g., catch/skip failed deposits into a claimable escrow rather than aborting the whole loop).

### Proof of Concept
1. Staker creates a staking contract with `operator` and nonzero `commission_percentage` via `create_staking_contract`.
2. `operator` calls `aptos_account::set_allow_direct_coin_transfers(false)` on a throwaway account `B` they control.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, B)` — succeeds with no validation (`staking_contract.move:810-838`).
4. Time passes, rewards accrue, and staker calls `unlock_stake` to withdraw part of their principal.
5. `unlock_stake` → `distribute_internal` attempts to pay operator's commission to beneficiary `B` via `aptos_account::deposit_coins`; because `B` has direct transfers disabled, this aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts, so the staker's unlocked principal is never actually withdrawn, and this repeats for every future `distribute`/`unlock_stake`/`switch_operator` call as long as commission remains unpaid, indefinitely trapping the staker's funds.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-690)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-796)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-925)
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
```

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
