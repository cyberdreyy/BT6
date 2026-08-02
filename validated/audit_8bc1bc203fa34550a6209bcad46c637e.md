### Title
Permissionless `staking_contract::distribute` can be permanently DoS'd by a single unregistered/opted-out recipient in the shared `distribution_pool`, stranding the staker's own unlocked stake — (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute_internal` iterates over every shareholder in a single shared `distribution_pool` (which holds pending payouts for both the staker's principal and the operator's commission/beneficiary) and calls `aptos_account::deposit_coins` for each one in a `while` loop, with no way to skip, remove, or isolate a failing recipient [1](#0-0) . This mirrors the external report's bug class exactly: an Enumerable/Pool-like structure of recipients that keeps growing but has no removal mechanism, and where every entry receives an unconditional transfer call during shared distribution.

### Finding Description
`distribute()` is explicitly permissionless ("Allow anyone to distribute already unlocked funds") [2](#0-1) . Internally it withdraws the pool's total unlocked coins, updates the `distribution_pool`, then loops:

```
while (distribution_pool.shareholders_count() > 0) {
    let recipients = distribution_pool.shareholders();
    let recipient = recipients[0];
    ...
    if (recipient == operator) { recipient = beneficiary_for_operator(operator); };
    aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
    ...
}
``` [1](#0-0) 

`aptos_account::deposit_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account already exists but has not registered a `CoinStore<CoinType>` and has opted out of direct transfers via `can_receive_direct_coin_transfers` [3](#0-2) . Because this only auto-registers/creates the account when `!account::exists_at(to)`, an *existing* account (one that already transacted, e.g. paying gas purely via the fungible-asset primary store rather than legacy `CoinStore<AptosCoin>`) that never registered `CoinStore<AptosCoin>` and has called `set_allow_direct_coin_transfers(false)` will cause this deposit to abort.

`set_beneficiary_for_operator` lets an operator — an unprivileged role relative to the staker's funds — freely redirect commission payouts to any address they choose [4](#0-3) . If the operator points the beneficiary at an account that is unregistered for `CoinStore<AptosCoin>` and has disabled direct coin transfers, every subsequent call to `distribute()` for that staker/operator pair will abort inside the shared `while` loop the moment it reaches the beneficiary's entry — because the whole entry function is one atomic transaction, an abort anywhere rolls back the entire withdrawal, including the portion meant for the staker's own already-unlocked principal.

There is no mechanism in `staking_contract.move` to remove a poisoned shareholder from `distribution_pool`, skip a failing recipient, or route around a permanently-unpayable beneficiary — the same missing-removal defect the external report flags for `protocolFeeRecipients`.

### Impact Explanation
This can permanently strand a staker's own unlocked stake: once `unlock_stake`/`request_commission` have queued entries in the shared `distribution_pool`, the staker cannot get *any* of the pooled payout (not just the operator's blocked share) because `distribute()` always processes the whole pool atomically [5](#0-4) , [6](#0-5) . This is a "permanent lock / non-recoverable loss of claim rights" and "wrong-role control ... beneficiary" style impact called out in the task's required-impact list: an operator, without holding the staker's role, can indefinitely block the staker's withdrawal rights by weaponizing the beneficiary mechanism.

### Likelihood Explanation
Requires only actions the operator already legitimately controls (`set_beneficiary_for_operator`) plus preparing a receiving account that (a) already exists on-chain, (b) has never registered `CoinStore<AptosCoin>`, and (c) has called `set_allow_direct_coin_transfers(false)`. Because gas fees on current Aptos can be paid from the fungible-asset primary store rather than the legacy `CoinStore<AptosCoin>`, condition (a)+(b) is plausible for accounts created post-FA-migration, but I could not fully verify from the indexed code whether `CoinStore<AptosCoin>` registration is still unconditionally forced somewhere else in the account-creation/gas-payment path (e.g., `transaction_validation.move`) for mainnet transactions. This is the key uncertainty limiting confidence from "high" to "medium-high": if some other path still force-registers `CoinStore<AptosCoin>` for any account that ever pays gas, the precondition becomes harder to satisfy and the operator would need to target a completely dormant/never-transacted beneficiary address that they only "activate" by calling `set_allow_direct_coin_transfers` from that very address without ever registering the coin — which is self-consistent but should be validated with a live/local test before treating this as confirmed exploitable on mainnet.

### Recommendation
- In `distribute_internal`, wrap the `aptos_account::deposit_coins` call in a way that isolates per-recipient failures (e.g., use `coin::deposit` with a fallback path, or catch unregistered/opted-out recipients and route their share to a claimable "pending" store instead of aborting the whole loop).
- Alternatively, disallow setting `beneficiary_for_operator` to an address that is not currently registered/able to receive `AptosCoin`, and re-validate this invariant whenever the beneficiary is used, not just when set.
- Consider giving the staker a way to bypass/exclude the operator/beneficiary's distribution and withdraw their own share independent of the operator's payout status, i.e., avoid one shared atomic distribution covering multiple independent claim rights.

### Proof of Concept
1. Staker creates a `staking_contract` with `operator` O and some `commission_percentage` via `create_staking_contract` [7](#0-6) .
2. O calls `set_beneficiary_for_operator(O, B)` where B is an address that exists on-chain but has never registered `CoinStore<AptosCoin>` [8](#0-7) .
3. From B, call `aptos_account::set_allow_direct_coin_transfers(false)` (function referenced in `aptos_account.move`, not fully inlined here — presence confirmed via grep but body not shown in this trace).
4. Staker calls `unlock_stake` (which internally calls `distribute_internal` to flush any existing inactive stake and also queues future commission), or O calls `request_commission`, adding a distribution entry for B into `staking_contract.distribution_pool` [9](#0-8) .
5. After lockup expiry, anyone calls `distribute(staker, operator)`. When the loop reaches B's entry, `aptos_account::deposit_coins(B, ...)` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` [10](#0-9) , reverting the entire `distribute` transaction — including the staker's own portion — with no way to remove B or otherwise complete the staker's withdrawal.

Note: step 3's exact preconditions for "existing but `CoinStore<AptosCoin>`-unregistered" accounts on current mainnet could not be fully confirmed within available indexed files (gas-payment/account-creation invariants live outside the searched scope), so this should be verified with a local devnet reproduction before being treated as a confirmed, unconditional finding.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L805-838)
```text






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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-887)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L991-998)
```text
        stake::initialize_stake_owner(&stake_pool_signer, 0, operator, voter);

        // Extract owner_cap from the StakePool, so we have control over it in the staking_contracts flow.
        // This is stored as part of the staking_contract. Thus, the staker would not have direct control over it without
        // going through well-defined functions in this module.
        let owner_cap = stake::extract_owner_cap(&stake_pool_signer);

        (stake_pool_signer, stake_pool_signer_cap, owner_cap)
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
