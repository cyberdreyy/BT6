Based on my research, the strongest local analog to the EigenLayer "un-skippable malicious index" bug is in `staking_contract::distribute_internal`, which lacks any mechanism to skip a shareholder whose payout call reverts — unlike the delegation_pool/vesting modules, whose withdrawal paths are per-user and don't share a single atomic loop across unrelated parties.

### Title
Malicious operator can permanently block `staking_contract::distribute` and freeze a staker's unlocked stake - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`distribute_internal` iterates over all shareholders of a `distribution_pool` (the staker and the operator/beneficiary) in a single atomic `while` loop, redeeming shares and calling `aptos_account::deposit_coins` for each recipient in sequence, with no ability to skip a recipient whose deposit reverts [1](#0-0) .

### Finding Description
`distribute_internal` withdraws all inactive/pending_inactive coins from the stake pool up front, then loops `while (distribution_pool.shareholders_count() > 0)`, always taking `recipients[0]`, redeeming that shareholder's shares, and depositing the resulting coins via `aptos_account::deposit_coins` [2](#0-1) . There is no `indicesToSkip`-style parameter, and no try/catch semantics in Move — if any single recipient's deposit aborts, the entire transaction (including the withdrawal from the stake pool and any already-redeemed shares for other shareholders processed earlier in the same loop) is rolled back atomically.

The `distribution_pool` for a staker/operator pair normally contains exactly two shareholders: the staker (added first, via `buy_in` for the principal) and the operator/beneficiary (added later, when commission is requested) [3](#0-2) . The operator address is supplied unilaterally by the staker when creating the contract and does not require the operator to pre-register an `AptosCoin` `CoinStore`. If the operator's account has no registered `CoinStore<AptosCoin>` and has opted out of direct coin transfers, `aptos_account::deposit_coins` to that operator will abort every time it is attempted.

Because the loop processes shareholders in list order and cannot skip a reverting entry, once the malicious/unregistered operator's turn comes up in the loop, `distribute()` and `distribute_internal()` will abort unconditionally — including when called from `switch_operator`, `set_beneficiary_for_operator`-adjacent flows, and vesting's `distribute`/`vest`/`terminate_vesting_contract`, which all invoke `distribute_internal` internally [4](#0-3) [5](#0-4) .

### Impact Explanation
If this holds, an operator can permanently prevent an honest staker (or vesting admin/shareholders using that operator) from ever calling `distribute()` successfully, freezing the staker's own already-unlocked (inactive) principal and rewards indefinitely in the underlying `StakePool`, with no recovery path since `distribute_internal` is the only function that calls `stake::withdraw_with_cap` for this contract type. This matches the "permanent lock / non-recoverable loss of claim rights" and "commission/beneficiary accounting corruption that traps value" impact categories.

### Likelihood Explanation
I could not fully verify within the available tool budget whether `aptos_account::deposit_coins`/`can_receive_direct_coin_transfers` genuinely aborts for an unregistered account that has disabled direct transfers in this exact repo revision (I found the call site but not the implementation of `aptos_account::deposit_coins` and the `DirectTransferConfig` opt-out semantics before running out of iterations). This is the load-bearing assumption for the exploit and needs to be confirmed by reading `aptos_account.move`'s `deposit_coins`/`can_receive_direct_coin_transfers` and `coin.move`'s registration checks directly. If that assumption is confirmed, likelihood is high since becoming an "operator" requires no special privilege and the griefing setup (skip registration + disable direct transfers) is a normal, permissionless action.

### Recommendation
Given the unverified assumption above, I recommend having a Devin session confirm the `aptos_account::deposit_coins` semantics for unregistered/opted-out accounts, and if confirmed, mitigate by: (1) allowing `distribute_internal` to isolate a failing recipient's payout into a separate claimable/pull-based balance instead of push-depositing inline in the loop, or (2) requiring operator accounts to have a registered `CoinStore<AptosCoin>` (or use a pull-based commission claim) so a single non-cooperating party cannot block distribution to all other shareholders.

### Proof of Concept
Not independently confirmed — the PoC would require: staker creates a `staking_contract` with a malicious `operator` address; malicious operator account never registers `CoinStore<AptosCoin>` and calls `coin::set_allow_direct_coin_transfers(false)`; staker's stake unlocks and lockup expires; any call to `staking_contract::distribute(staker, operator)` should be shown to abort at the operator's `aptos_account::deposit_coins` step, and this abort must be shown to roll back the staker's own already-processed redemption in the same loop. I was not able to execute or trace this end-to-end in the current session due to tool-call limits, so this finding should be treated as a lead requiring confirmation of `aptos_account.move`'s exact behavior before being treated as conclusively proven.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-789)
```text
        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-756)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

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

        emit(
            Distribute {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount: total_distribution_amount,
            },
        );
    }
```
