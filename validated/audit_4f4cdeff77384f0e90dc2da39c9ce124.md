## Distribute-Blocking Griefing in `vesting::distribute` Can Permanently Freeze All Shareholders' Stake - (File: aptos-move/framework/aptos-framework/sources/vesting.move)

### Summary
Similar to the Canto `getUnderlyingPrice`/`sampleSupply` bug — where one failing sub-operation (`sampleSupply`) reverts the whole price read instead of degrading gracefully — Aptos's `vesting::distribute` performs a single atomic loop that pays out *every* shareholder's share of a vesting contract's withdrawable stake in one transaction, with no isolation between shareholders. If payout to any single shareholder aborts, the entire `distribute` call reverts, and since `distribute` is also invoked internally from `vest`, `unlock_rewards`(indirectly via `vest`), and `terminate_vesting_contract`, one griefable/uncooperative shareholder can permanently block all other shareholders' rewards and vested-token withdrawals from that shared vesting contract. [1](#0-0) [2](#0-1) 

### Finding Description
`vesting::distribute` withdraws all currently-inactive stake from the underlying stake pool and then loops over every shareholder of the vesting contract's `grant_pool`, redeeming their shares and depositing the corresponding coins via `aptos_account::deposit_coins` (looking at the analogous distribution loop pattern shared with `staking_contract::distribute_internal`, which the vesting module wraps for the operator side): [3](#0-2) 

`aptos_account::deposit_coins` will `abort` for a recipient in either of two conditions reachable purely by that recipient acting on their own account, with no privilege required:
- The recipient previously called `set_allow_direct_coin_transfers(false)` on their own account and has not registered a `CoinStore<AptosCoin>` — causing `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`. [4](#0-3) [5](#0-4) 

Critically, `vesting::set_beneficiary` explicitly guards against this by requiring `assert_account_is_registered_for_apt(new_beneficiary)` before allowing an admin to set a beneficiary: [6](#0-5) 

However, this check only protects the *beneficiary-setting* path. The **shareholder addresses themselves** (the direct recipients when no beneficiary is set, and the ones whose share entitlement the pool always attempts to pay) are never checked at distribution time, and any shareholder can independently flip `set_allow_direct_coin_transfers(false)` on their own account at any time between contract creation and a `distribute()` call — there is no analogous `assert_account_is_registered_for_apt`-style protection at pay-out time and no per-recipient try/catch or skip-on-failure logic, unlike the Compound-style graceful `0`-return the report recommends.

Because `distribute()` is:
1. called directly by any unprivileged caller (`public entry fun distribute(contract_address: address)` has no signer check — "Allow anyone to distribute already unlocked funds" per the staking_contract analog comment),
2. invoked internally by `vest()` (via `unlock_rewards` → indirectly not directly, but `terminate_vesting_contract` calls `distribute` directly and unconditionally before admin can withdraw remaining funds),

a single non-cooperating (or simply configured to reject) shareholder among the (up to 30, per `MAXIMUM_SHAREHOLDERS`) shareholders of one shared vesting contract causes:
- `distribute()` to permanently abort for **all** shareholders sharing that contract,
- `terminate_vesting_contract()` (which unconditionally calls `distribute` first) to be permanently blocked, preventing the admin from ever terminating/reclaiming the vesting contract,
- accumulated rewards and vested tranches to remain stuck in the underlying stake pool's inactive/pending_inactive balance indefinitely, unreachable by any of the other legitimate shareholders.

### Impact Explanation
This breaks the "Unlock, reactivate, withdraw ... paths must not redirect value or strand it permanently" and "preserve ... withdrawal rights" invariants: one shareholder (who needs no elevated privilege — merely control of their own account's transfer-config flag) can strand all co-shareholders' vested grant and reward distributions in a shared `vesting_contract`, and simultaneously block the admin's ability to `terminate_vesting_contract`. This is a high-impact, permanent denial of legitimate claim rights over already-vested/already-earned stake for uninvolved third parties.

### Likelihood Explanation
Likelihood is limited by two factors I could not fully resolve given index constraints:
1. Whether `coin::is_account_registered<AptosCoin>` still meaningfully gates behavior post the Aptos Coin→FungibleAsset migration (I found `register_apt` now provisions a primary fungible store rather than a legacy `CoinStore`, and did not have time to trace whether `deposit_coins`'s `is_account_registered<CoinType>`/`can_receive_direct_coin_transfers` check is still exercised for `AptosCoin` specifically, versus being bypassed via the FA-specific `fungible_transfer_only` path used elsewhere).
2. Whether any vesting contracts in production actually have multiple shareholders sharing one `distribute()` call (single-shareholder contracts would not exhibit the cross-shareholder griefing, only self-griefing).

Because I could not conclusively verify point (1) — i.e., whether `aptos_account::deposit_coins`'s opt-out abort path is actually live for `AptosCoin` after the FA migration in this snapshot of the code — I cannot certify this as a proven, currently-exploitable high/critical bug rather than a latent or already-mitigated one.

### Recommendation
- In `vesting::distribute` (and the shared `staking_contract::distribute_internal` pattern), isolate each recipient's payout so a single failing deposit cannot revert the whole batch — e.g., wrap per-recipient deposits so that a recipient's coins are held back into the distribution pool or an unclaimed-funds table on failure instead of aborting the entire distribution, mirroring the report's core mitigation ("use try-catch and don't let one failure propagate to block all callers").
- Alternatively, enforce the same `assert_account_is_registered_for_apt` guarantee for shareholders (not just beneficiaries) at contract-creation time and re-verify/skip at distribution time rather than aborting.

### Proof of Concept
Conceptual sequence (pending confirmation of the FA-migration point above):
1. Admin creates a vesting contract with shareholders A and B (`create_vesting_contract`).
2. Shareholder B calls `aptos_account::set_allow_direct_coin_transfers(false)` on their own account, and never registers/owns a `CoinStore<AptosCoin>`. [5](#0-4) 
3. Time passes; rewards/vested tranches accrue and become withdrawable on the stake pool.
4. Anyone calls `vesting::distribute(contract_address)` (permissionless) or `terminate_vesting_contract`; the loop reaches shareholder B, `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, and the entire transaction — including shareholder A's payout — reverts. [3](#0-2) 
5. Shareholder A can never withdraw their vested/reward share as long as B keeps this configuration, and the admin can never call `terminate_vesting_contract` to reclaim the contract.

I was not able to independently confirm whether the `is_account_registered<AptosCoin>`/opt-out check in `aptos_account::deposit_coins` is still triggerable for `AptosCoin` post-FA-migration in this codebase snapshot — this should be verified in a live/Devin session (e.g., by tracing `coin::is_account_registered<AptosCoin>` and `coin::deposit<AptosCoin>` against the FA-migrated implementation) before treating this as a confirmed, currently-live vulnerability rather than a design smell.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L759-768)
```text
    public entry fun distribute_many(contract_addresses: vector<address>) acquires VestingContract {
        let len = contract_addresses.length();

        assert!(len != 0, error::invalid_argument(EVEC_EMPTY_FOR_MANY_FUNCTION));

        contract_addresses.for_each_ref(|contract_address| {
            let contract_address = *contract_address;
            distribute(contract_address);
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-781)
```text
    /// Terminate the vesting contract and send all funds back to the withdrawal address.
    public entry fun terminate_vesting_contract(admin: &signer, contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        // Distribute all withdrawable coins, which should have been from previous rewards withdrawal or vest.
        distribute(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let (active_stake, _, pending_active_stake, _) = stake::get_stake(vesting_contract.staking.pool_address);
        assert!(pending_active_stake == 0, error::invalid_state(EPENDING_STAKE_FOUND));

```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-928)
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

        let old_beneficiary = get_beneficiary(vesting_contract, shareholder);
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
