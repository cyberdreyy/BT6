## Summary of Analysis

The Solidity bug lets the *auction winner* revert `claimAuction()` via a hostile ERC-721 receive hook, blocking payouts to **all** other bidders in the same atomic loop. The Move-native analog is `aptos_framework::staking_contract::distribute_internal`, which iterates over a shared `distribution_pool` (staker + operator/beneficiary shares) and calls `aptos_account::deposit_coins` for **every** recipient in one atomic transaction. If any single recipient's deposit aborts, the whole transaction — including every other recipient's payout and the coins already withdrawn from the stake pool — is rolled back.

### Title
Operator can permanently brick commission distribution for all stakers via a "poisoned" beneficiary address, trapping staker stake - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Finding Description
`distribute_internal` withdraws all withdrawable stake from the pool and then loops over every shareholder in `distribution_pool`, calling `aptos_account::deposit_coins` for each one in the same transaction: [1](#0-0) 

When the recipient is the operator, the payout is redirected to `beneficiary_for_operator(operator)`: [2](#0-1) 

The operator globally controls this beneficiary address for *all* staking contracts it participates in, via a permissionless entry function that requires no staker cooperation: [3](#0-2) 

`aptos_account::deposit_coins`/`transfer_coins` will abort if the target account is **not yet registered** for `AptosCoin` and has explicitly disabled direct coin transfers (`can_receive_direct_coin_transfers` returns `false`): [4](#0-3) 

An operator can create a resource account it controls, never register it for `AptosCoin`, and call `set_allow_direct_coin_transfers(&resource_signer, false)` on it (a permissionless call), then set it as its beneficiary via `set_beneficiary_for_operator`. From that point on, **every** call to `distribute()` for **any** staker paired with this operator will abort partway through the payout loop, because the deposit to the poisoned beneficiary always fails.

Critically, `switch_operator` — the staker's only way to move away from a hostile operator — first force-calls `distribute_internal` on the *old* operator to flush any pending inactive stake: [5](#0-4) 

Since that forced `distribute_internal` call hits the same poisoned beneficiary path, it also aborts, meaning the staker cannot even switch operators to escape once inactive stake exists in the pool. Because Move transactions are fully atomic, the abort also unwinds the `stake::withdraw_with_cap` that already pulled the coins out of the stake pool, so the funds fall back into the pool state but distribution can never again succeed — leaving that inactive stake permanently unreachable via `distribute`, `switch_operator`, or (for vesting-backed contracts) `vesting::distribute`/`terminate_vesting_contract`, since vesting relies on the same `staking_contract::distribute` path.

### Impact Explanation
This breaks the invariant that "unlock/withdraw/synchronize paths must not strand [stake] permanently." An operator, without any special privilege over the staker's funds, can permanently trap the staker's own already-unlocked (inactive) stake and prevent them from either collecting it or firing the operator. This affects every staker who has ever selected (or been assigned, e.g. via a vesting admin) this operator, making it a high-severity, unprivileged-triggerable denial of value.

### Likelihood Explanation
Likelihood is moderate-to-high: the attack requires only that the malicious party operate as an "operator" for at least one staking contract — a role commonly filled by third-party validator/staking services that stakers do not fully vet. No collusion with the staker or delegation-pool voter is needed, and the poisoning step (`set_allow_direct_coin_transfers(false)` + `set_beneficiary_for_operator`) is cheap and fully within the operator's own account. The only prerequisite is that the poisoned beneficiary address never registers a `CoinStore<AptosCoin>` (achievable by using a fresh resource account that never receives/sends APT).

### Recommendation
`distribute_internal` should isolate each recipient's payout so a failure for one shareholder does not revert the whole batch — e.g., use a "pull" pattern (credit an internal claimable balance per shareholder) instead of push-transferring inside a shared loop, or wrap each `aptos_account::deposit_coins` call so failures are captured/skipped (leaving that shareholder's shares un-redeemed for later retry) rather than aborting the entire distribution. The forced `distribute_internal` inside `switch_operator` should similarly not be allowed to block the operator switch itself.

### Proof of Concept
1. Attacker creates resource account `R` via `account::create_resource_account`, keeping the `SignerCapability`; `R` is never registered for `AptosCoin`.
2. Attacker calls `aptos_account::set_allow_direct_coin_transfers(&R_signer, false)`.
3. Attacker (as `operator`) calls `staking_contract::set_beneficiary_for_operator(operator, R)`.
4. A staker creates (or already has) a `staking_contract` with this `operator`; stake accrues rewards, commission is requested (`request_commission`), and lockup expires.
5. Anyone calls `staking_contract::distribute(staker, operator)`. In `distribute_internal`, when the loop reaches the operator's share, it redirects to `beneficiary_for_operator(operator) == R`, and `aptos_account::deposit_coins` aborts on `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` because `R` is unregistered and has disabled direct transfers.
6. The whole transaction reverts — the staker's own share is not paid out either.
7. Staker attempts `switch_operator` to escape; this calls `distribute_internal` first (line 785 of `staking_contract.move`), which aborts identically, so the staker cannot switch away either. The inactive stake remains permanently stuck.

Note: I was unable to fully view the very top portion of `aptos_account.move` (the `deposit_coins`/`transfer_coins`/`set_allow_direct_coin_transfers` full bodies) due to index truncation; the control-flow described is reconstructed from the visible tail of `deposit_coins` and the corresponding `aptos_account.spec.move` abort conditions (`DepositAbortsIf`, `can_receive_direct_coin_transfers`). A Devin session with full repo access should confirm the exact line numbers and function bodies before filing/fixing.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-797)
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-131)
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
    }
```
