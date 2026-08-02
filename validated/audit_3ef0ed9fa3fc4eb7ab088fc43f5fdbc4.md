## Title
Unvalidated `new_beneficiary` in `staking_contract::set_beneficiary_for_operator` can permanently brick `distribute()`, trapping staker's unlocked stake - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator repoint their commission payouts to any arbitrary `new_beneficiary` address with zero validation that the address is registered/able to receive APT. [1](#0-0) 
This mirrors the external report's bug class exactly: a sensitive setter accepts an unchecked address argument. Notably, the sibling function `vesting::set_beneficiary` *does* validate the target with `assert_account_is_registered_for_apt(new_beneficiary)` before accepting it, but `staking_contract::set_beneficiary_for_operator` has no equivalent check. [2](#0-1) 

### Finding Description
`distribute_internal` pays out both the staker's unlocked stake and the operator's (or its beneficiary's) commission in a single atomic loop over the shared `distribution_pool`: [3](#0-2) 
Each recipient, including the beneficiary, is paid via `aptos_account::deposit_coins`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is not yet registered for the coin type and has explicitly opted out of direct transfers via `set_allow_direct_coin_transfers(false)`. [4](#0-3) 
Because `set_beneficiary_for_operator` performs no validation on `new_beneficiary`, an operator can set it to an address that has disabled direct transfers (or otherwise cannot be deposited to). Since the payout loop is not per-recipient fault-isolated — it is one Move function call over the whole `distribution_pool` — a failure to pay the beneficiary aborts the *entire* transaction, including the staker's own share of already-unlocked (`inactive`/`pending_inactive`) stake.

All entry points that reach `distribute_internal` — `distribute`, `request_commission_internal` (called from `request_commission` and `switch_operator`) — go through this same atomic path, so once the beneficiary is poisoned this way, none of them can succeed. [5](#0-4) [6](#0-5) 

### Impact Explanation
This traps value belonging to the staker — an account that never opted into the malicious beneficiary and has no direct capability to bypass `staking_contract`'s custody (the underlying stake pool signer capability is held internally; the staker cannot call `stake::withdraw` directly). Once `distribute()` (and therefore `switch_operator`/`request_commission`, which force a distribution first) permanently reverts, the staker's already-unlocked/inactive stake becomes non-recoverable through any exposed entry function. This matches the "Permanent lock or non-recoverable loss of claim rights in stake... flows" and "beneficiary payout... corruption that... traps value" impact categories.

### Likelihood Explanation
Setting the beneficiary is a single, unprivileged-relative-to-the-staker, entry-function call by the operator (`set_beneficiary_for_operator`) gated only by the `operator_beneficiary_change_enabled` feature flag — no staker consent is required. An operator (who is not necessarily trusted by the staker beyond running validator infrastructure) can trigger this at any time. However, I was unable to fully confirm within the available context whether, under the ongoing Coin→FungibleAsset migration, `coin::is_account_registered<AptosCoin>` for APT always returns `true` (which would make the `can_receive_direct_coin_transfers` check unreachable for APT specifically and neutralize this exact griefing vector for the AptosCoin case). I could not locate/verify `coin::is_account_registered`'s current implementation in the indexed code. If it is not always `true` for FA-migrated APT, then the vector above applies as described; if it is always `true` for APT, this specific path is not exploitable for AptosCoin and only applies to custom coin types.

### Recommendation
- Add input validation to `set_beneficiary_for_operator` in `staking_contract.move` mirroring `vesting::set_beneficiary`, e.g. `assert_account_is_registered_for_apt(new_beneficiary)` (or equivalently require `can_receive_direct_coin_transfers(new_beneficiary)`), and disallow `new_beneficiary == @0x0`.
- More fundamentally, make `distribute_internal`'s payout loop fault-tolerant: isolate each recipient's deposit (e.g., via a nested call that can fail without reverting the whole loop, falling back to holding the failed share for later retry) so a single bad beneficiary/recipient cannot block payouts to all other shareholders, including the staker.

### Proof of Concept
Given the ambiguity noted above about `coin::is_account_registered` for APT post-FA-migration, I could not construct and verify an end-to-end PoC transaction sequence with full confidence using only the indexed code available to me. Conceptually the sequence would be:
1. Staker creates a `staking_contract` with `operator` and stakes funds; validator joins and earns rewards; staker calls `unlock_stake` to move some stake to `pending_inactive`/`inactive`.
2. A third-party account `V` (or an account controlled to test this) calls `aptos_account::set_allow_direct_coin_transfers(false)` and remains unregistered for AptosCoin.
3. `operator` calls `staking_contract::set_beneficiary_for_operator(operator, V)` — this succeeds with no validation. [1](#0-0) 
4. Once the pool's lockup expires and any account calls `distribute(staker, operator)`, `distribute_internal` attempts to pay `V` and aborts on `aptos_account::deposit_coins`, reverting the whole call and blocking the staker's inactive stake withdrawal as well. [3](#0-2) 

Given the uncertainty about whether this abort path is actually reachable for AptosCoin under the current FA-migrated `coin` module (which I could not verify from the indexed context), I recommend a Devin session with full repo/terminal access to compile and run this scenario against the local Move test framework (`staking_contract.move`'s existing `#[test]` harness) to confirm whether `deposit_coins` can actually abort for an AptosCoin beneficiary in the current codebase before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-805)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-923)
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
