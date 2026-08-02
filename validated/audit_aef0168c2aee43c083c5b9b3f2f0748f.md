## Analysis: Griefing shareholder can permanently freeze `vesting::distribute()` for all other shareholders

### Root Cause Analysis

The Gravity Bridge bug is a class of "one malicious permissionless input poisons a shared batch/loop, causing the whole subsystem to halt". Searching Aptos stake/vesting flows for an analogous unprivileged, single-poisoned-item, whole-batch-abort pattern led to `vesting::distribute()`.

`vesting::distribute()` iterates over **every** shareholder in a vesting contract in a single atomic loop and unconditionally calls `aptos_account::deposit_coins` for each one: [1](#0-0) 

`aptos_account::deposit_coins` will auto-register an unregistered recipient for `AptosCoin`, **unless** the recipient has explicitly opted out of unsolicited coin transfers via `can_receive_direct_coin_transfers`, in which case it aborts: [2](#0-1) 

That opt-out flag is set by the fully permissionless, unprivileged entry function `set_allow_direct_coin_transfers`, callable by any account (including any vesting shareholder) on their own address: [3](#0-2) 

The `set_beneficiary` path in vesting.move does guard against unregistered *beneficiaries* by calling `assert_account_is_registered_for_apt` — but this only checks that the account is *registered*, not whether it currently *accepts direct transfers*, and it is only invoked when an admin explicitly assigns a beneficiary: [4](#0-3) 

Default shareholders (i.e., addresses that never had a beneficiary explicitly set) receive **no such check at all**, either at `create_vesting_contract` time or afterward. A shareholder can therefore, at any point after being enrolled in the vesting contract, call `aptos_account::set_allow_direct_coin_transfers(false)` on their own account. If that shareholder is not otherwise registered for `AptosCoin` at that time, every future call to `vesting::distribute()` (or `distribute_many`, `terminate_vesting_contract`, which also calls `distribute()` before releasing funds) will abort inside the shared loop the moment it reaches that shareholder — reverting the entire transaction and blocking distribution of vested/reward funds to **every other shareholder** in that same vesting contract.

### Impact

- Any single shareholder in a multi-shareholder vesting contract can unilaterally and permanently (until they choose to undo it) block `distribute()`, `unlock_rewards_many`/`distribute_many`, and `terminate_vesting_contract` for **all** co-shareholders of that contract, freezing their vested/reward claims.
- Funds already withdrawn from the stake pool into the vesting contract's resource account become stuck (not lost, but non-distributable) because the loop has no per-recipient failure isolation — one bad actor prevents the whole batch, mirroring the Gravity Bridge freeze pattern (one malformed item blocks all subsequent processing for the shared resource).
- This is entirely unprivileged: no special role, no attacker-held keys beyond the attacker's own account, and it's reachable purely through public entry functions (`set_allow_direct_coin_transfers`, then normal vesting operation).

### Likelihood

High — the preconditions are trivial: be (or become) a shareholder in a vesting contract with ≥2 shareholders, never register for `AptosCoin` via `coin::register`, and call the permissionless `set_allow_direct_coin_transfers(false)`. No cost beyond gas, and it is trivially repeatable/renewable by the griefer.

### Recommendation

`distribute()` should not let one recipient's `deposit_coins` failure abort the whole loop. Options:
- Wrap each recipient's payout so failures are caught/skipped (Move doesn't have try/catch, so this requires pre-checking `coin::is_account_registered` / `can_receive_direct_coin_transfers` per recipient before calling `deposit_coins`, and rerouting undeliverable funds to a claimable escrow instead of calling `deposit_coins` directly), or
- Require `assert_account_is_registered_for_apt`-style checks for **all** shareholders (not just explicitly-set beneficiaries) at `create_vesting_contract` time, and additionally check `can_receive_direct_coin_transfers` at distribution time, skipping (not aborting) griefing shareholders and holding their share until they re-enable receipt.

### Uncertainty

I was not able to fully trace whether `distribute_many`/`terminate_vesting_contract`'s callers (off-chain automation, admin tooling) have any operational retry/skip logic outside the Move contract that might mitigate this in practice — that would require inspection of off-chain distribution schedulers, which is outside the indexed Move framework code available to me.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-740)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-935)
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
        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            let beneficiary = beneficiaries.borrow_mut(&shareholder);
            *beneficiary = new_beneficiary;
        } else {
            beneficiaries.add(shareholder, new_beneficiary);
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
