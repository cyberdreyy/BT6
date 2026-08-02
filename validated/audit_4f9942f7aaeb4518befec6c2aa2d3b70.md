## Finding: Unvalidated Beneficiary Address Permanently Locks Staker Withdrawals - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` and its `delegation_pool` counterpart let an operator set an arbitrary `new_beneficiary` address with **no validation** that the address can actually receive `AptosCoin`. This mirrors the report's root cause (a critical config value accepted without a forbidden/invalid-value check), except here the un-validated value is the commission beneficiary. Because `distribute_internal` unconditionally pays out to this beneficiary inside the *same* atomic loop that also pays out every staker's unlocked stake, an operator can configure a beneficiary that makes `aptos_account::deposit_coins` abort, permanently freezing withdrawal of already-unlocked stake for every staker attached to that operator's `staking_contract`.

### Finding Description
`set_beneficiary_for_operator` stores `new_beneficiary` with zero checks: [1](#0-0) 

Compare this to the admin-driven vesting path, which explicitly guards against exactly this failure mode: [2](#0-1) 

`delegation_pool::set_beneficiary_for_operator` has the identical omission: [3](#0-2) 

The consequence surfaces in `distribute_internal`, which is called by `distribute`, `unlock_stake`, `unlock_rewards`, `request_commission`, `update_commision`, `switch_operator`, and `switch_operator_with_same_commission`. It iterates **every** shareholder in the distribution pool (stakers and operator/beneficiary alike) in one loop and pays each of them via `aptos_account::deposit_coins`, redirecting the operator's payout to the beneficiary: [4](#0-3) 

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` when the target account exists, has no registered `CoinStore<AptosCoin>`, and has explicitly opted out of direct coin transfers (via `aptos_account::set_allow_direct_coin_transfers(false)`). An operator fully controls the beneficiary address value and can trivially set it to such an account. Because the payout loop processes **all** shareholders in one transaction and the abort reverts the whole call, this doesn't just block the operator's own commission — it blocks distribution of the staker's own already-unlocked/inactive stake as well, since that payout happens in the very same loop/transaction.

### Impact Explanation
Once the operator sets a "rejecting" beneficiary:
- `distribute`, `unlock_stake`, `unlock_rewards`, `request_commission`, `update_commision`, `switch_operator*` all abort for that staker/operator pair, because they all invoke `distribute_internal` before doing anything else.
- The staker's own inactive/unlockable stake becomes **permanently stuck** — the staker has no way to force a payout that skips the poisoned beneficiary, since the beneficiary redirection is hard-coded and unconditional in `distribute_internal`.
- Recovery requires the operator to call `set_beneficiary_for_operator` again with a valid address; if the operator is malicious, negligent, or has lost access, staker funds are locked indefinitely.

This matches the required "Stake And Lockup" impact class: it traps value (staker's unlocked stake) and corrupts the commission/beneficiary payout path, all without the operator needing any elevated privilege beyond the permissionless `set_beneficiary_for_operator` entry function that any operator can call for itself.

### Likelihood Explanation
High. `set_beneficiary_for_operator` is a `public entry` function callable directly by any operator address, requires the `operator_beneficiary_change_enabled` feature flag only (a mainnet-enabled feature), and needs no other privilege. Constructing a rejecting address only requires the operator to create an ordinary account and call the already-public `aptos_account::set_allow_direct_coin_transfers(false)` on it — no special access needed.

### Recommendation
Before accepting `new_beneficiary` in both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`, validate that the address can actually receive `AptosCoin`, mirroring `vesting::set_beneficiary`'s `assert_account_is_registered_for_apt(new_beneficiary)` check (or equivalently check `aptos_account::can_receive_direct_coin_transfers`/already-registered `CoinStore`). Additionally, consider hardening `distribute_internal` so that a failed payout to one recipient (e.g., the beneficiary) cannot block payouts to unrelated stakers in the same distribution pool — e.g., by isolating the beneficiary payout or falling back to depositing into a claimable/pending balance instead of aborting the whole transaction.

### Proof of Concept
1. Operator `O` creates a staking contract with staker `S` via `staking_contract::create_staking_contract`, and `S` accrues rewards/unlocked stake.
2. `O` creates a fresh account `B`, and calls `aptos_account::set_allow_direct_coin_transfers(false)` from `B`'s signer (no `CoinStore<AptosCoin>` registered).
3. `O` calls `staking_contract::set_beneficiary_for_operator(O, B)` — succeeds, no validation performed.
4. `S` (or anyone) later calls `staking_contract::unlock_stake`, `unlock_rewards`, `distribute`, or `request_commission` for the `(S, O)` pair. Internally `distribute_internal` reaches the payout loop, attempts `aptos_account::deposit_coins(B, ...)` for the operator's commission share, and the transaction aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. Every subsequent attempt by `S` to withdraw their own unlocked stake through any of these entry points aborts identically, permanently locking `S`'s funds unless `O` cooperates by resetting the beneficiary. [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-829)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-924)
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

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1291)
```text
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };

        emit(SetBeneficiaryForOperator {
            operator: operator_addr,
            old_beneficiary,
            new_beneficiary,
        });
    }
```
