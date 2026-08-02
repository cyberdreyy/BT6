## Finding

### Title
Unprivileged shareholder can permanently block `vesting::distribute()` for all co-shareholders by disabling direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
The BendDAO report describes a borrower who revokes ERC20 approval so the bot-driven `_repay()` transfer reverts, blocking debt repayment/closure for that specific position. The Aptos-native analog is `vesting::distribute()`, a permissionless function that pays out a single shared pot of withdrawn stake to every shareholder of a vesting contract inside one atomic loop. Any one shareholder can unilaterally make their own leg of that loop abort (by disabling direct coin transfers on an account that doesn't yet hold an `AptosCoin` `CoinStore`), which reverts the whole transaction and blocks payout to every other shareholder in the same vesting contract, indefinitely.

### Finding Description
`vesting::distribute()` withdraws all unlocked stake as a single `Coin<AptosCoin>` and pays every shareholder in one loop: [1](#0-0) 

Each payment goes through `aptos_account::deposit_coins`, which only skips the opt-in check when the recipient already has a registered `CoinStore<AptosCoin>`: [2](#0-1) 

If the recipient is **not yet registered** for `AptosCoin`, `deposit_coins` requires `can_receive_direct_coin_transfers(to)` to be true. This flag is controlled by the `DirectTransferConfig` resource, which any account holder can set to `false` themselves via the unprivileged `set_allow_direct_coin_transfers` entry function: [3](#0-2) 

If a shareholder address exists on-chain but has never registered an `AptosCoin` store (e.g., it was created via a path other than `aptos_account::create_account`, such as publishing a resource account or being referenced before ever transacting), that shareholder can call `set_allow_direct_coin_transfers(false)`. From that point on, `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` whenever `distribute()` tries to pay them — and because the `for_each_ref` loop over shareholders is inside one atomic transaction, the abort reverts the entire `distribute()` call, not just that shareholder's leg.

This exactly mirrors the BendDAO bug class: an unprivileged party revokes a permission (approval in Solidity, direct-transfer opt-in in Move) that a "bot"/permissionless keeper function depends on, causing the shared operation to fail and value to be stuck.

### Impact Explanation
Once unlocked, stake sits as `inactive`/`pending_inactive` funds in the underlying stake pool, no longer earning validator rewards. If `distribute()` cannot complete because of one shareholder's opt-out, **all other shareholders** of that vesting contract are denied their already-unlocked, non-yielding payout indefinitely — there is no way to skip or isolate the failing recipient inside `distribute()`. This traps value and non-recoverably delays claim rights for parties who did nothing wrong, matching "permanent lock or non-recoverable loss of claim rights in ... vesting flows" and "share-accounting corruption ... that traps value."

### Likelihood Explanation
Likelihood is limited by the precondition that the malicious/uncooperative shareholder's address must not yet have an `AptosCoin` `CoinStore` registered when they call `set_allow_direct_coin_transfers(false)` — most externally-owned accounts get auto-registered for APT on first `aptos_account`-mediated transfer. However, addresses that are pre-designated as vesting shareholders before ever transacting, or resource/object accounts, are plausible unregistered targets, and the attacker fully controls their own account's `DirectTransferConfig`. No special privilege is required beyond owning one shareholder slot in a shared vesting contract.

### Recommendation
Do not let a single recipient's opt-out revert payouts to all other shareholders. Isolate per-recipient transfer failures (e.g., wrap each `aptos_account::deposit_coins` call so a failure for one shareholder falls back to holding/escrowing that shareholder's share for later claim, rather than aborting the whole `distribute()` transaction), mirroring the BendDAO fix of separating "reduce debt with what's collectible" from "the action that can be blocked by one party."

### Proof of Concept
1. Admin creates a vesting contract with shareholders `S1` (normal, registered for APT) and `S2` (an address that exists on-chain but has never registered a `CoinStore<AptosCoin>`).
2. `S2` calls `aptos_account::set_allow_direct_coin_transfers(false)` (unprivileged, self-signed) — see [4](#0-3) .
3. Time passes, rewards/vested amounts accumulate and get unlocked via `vest()`/`unlock_rewards()` (`stake::unlock_with_cap`), landing in `inactive`/`pending_inactive` state — see [5](#0-4) .
4. Anyone (a bot or the admin) calls `vesting::distribute(contract_address)`. The loop reaches `S2`'s share, calls `aptos_account::deposit_coins(S2, ...)`, which aborts because `S2` is unregistered and has opted out of direct transfers.
5. The whole transaction reverts: `S1` (and any other honest shareholders) receive nothing, and the unlocked, non-yielding stake remains stuck until `S2` either registers a `CoinStore<AptosCoin>` or re-enables direct transfers — actions only `S2` can take.

**Note on staking_contract.move:** the same permissionless `distribute()`/`distribute_internal()` pattern exists for staking contracts with pool-based commission/withdrawal distribution to multiple stakeholders ( [6](#0-5) ), but I was unable to fully retrieve the body of `distribute_internal` beyond line 870 to confirm whether it also performs a shared, single-transaction payout loop via `aptos_account::deposit_coins`/`transfer`. That path should be reviewed for the identical issue, but I'm not asserting it here without direct code confirmation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L635-704)
```text
    /// Unlock any accumulated rewards.
    public entry fun unlock_rewards(contract_address: address) acquires VestingContract {
        let accumulated_rewards = total_accumulated_rewards(contract_address);
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        unlock_stake(vesting_contract, accumulated_rewards);
    }

    /// Call `unlock_rewards` for many vesting contracts.
    public entry fun unlock_rewards_many(contract_addresses: vector<address>) acquires VestingContract {
        let len = contract_addresses.length();

        assert!(len != 0, error::invalid_argument(EVEC_EMPTY_FOR_MANY_FUNCTION));

        contract_addresses.for_each_ref(|contract_address| {
            let contract_address: address = *contract_address;
            unlock_rewards(contract_address);
        });
    }

    /// Unlock any vested portion of the grant.
    public entry fun vest(contract_address: address) acquires VestingContract {
        // Unlock all rewards first, if any.
        unlock_rewards(contract_address);

        // Unlock the vested amount. This amount will become withdrawable when the underlying stake pool's lockup
        // expires.
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        // Short-circuit if vesting hasn't started yet.
        if (vesting_contract.vesting_schedule.start_timestamp_secs > timestamp::now_seconds()) {
            return
        };

        // Check if the next vested period has already passed. If not, short-circuit since there's nothing to vest.
        let vesting_schedule = &mut vesting_contract.vesting_schedule;
        let last_vested_period = vesting_schedule.last_vested_period;
        let next_period_to_vest = last_vested_period + 1;
        let last_completed_period =
            (timestamp::now_seconds() - vesting_schedule.start_timestamp_secs) / vesting_schedule.period_duration;
        if (last_completed_period < next_period_to_vest) {
            return
        };

        // Calculate how much has vested, excluding rewards.
        // Index is 0-based while period is 1-based so we need to subtract 1.
        let schedule = &vesting_schedule.schedule;
        let schedule_index = next_period_to_vest - 1;
        let vesting_fraction = if (schedule_index < schedule.length()) {
            schedule[schedule_index]
        } else {
            // Last vesting schedule fraction will repeat until the grant runs out.
            schedule[schedule.length() - 1]
        };
        let total_grant = vesting_contract.grant_pool.total_coins();
        let vested_amount = fixed_point32::multiply_u64(total_grant, vesting_fraction);
        // Cap vested amount by the remaining grant amount so we don't try to distribute more than what's remaining.
        vested_amount = min(vested_amount, vesting_contract.remaining_grant);
        vesting_contract.remaining_grant -= vested_amount;
        vesting_schedule.last_vested_period = next_period_to_vest;
        unlock_stake(vesting_contract, vested_amount);

        emit(
            Vest {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                staking_pool_address: vesting_contract.staking.pool_address,
                period_vested: next_period_to_vest,
                amount: vested_amount,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-740)
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
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L20-42)
```text
    /// Account does not exist.
    const EACCOUNT_NOT_FOUND: u64 = 1;
    /// Account is not registered to receive APT.
    const EACCOUNT_NOT_REGISTERED_FOR_APT: u64 = 2;
    /// Account opted out of receiving coins that they did not register to receive.
    const EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS: u64 = 3;
    /// Account opted out of directly receiving NFT tokens.
    const EACCOUNT_DOES_NOT_ACCEPT_DIRECT_TOKEN_TRANSFERS: u64 = 4;
    /// The lengths of the recipients and amounts lists don't match.
    const EMISMATCHING_RECIPIENTS_AND_AMOUNTS_LENGTH: u64 = 5;

    /// Configuration for whether an account can receive direct transfers of coins that they have not registered.
    ///
    /// By default, this is enabled. Users can opt-out by disabling at any time.
    struct DirectTransferConfig has key {
        allow_arbitrary_coin_transfers: bool,
        update_coin_transfer_events: EventHandle<DirectCoinTransferConfigUpdatedEvent>
    }

    /// Event emitted when an account's direct coins transfer config is updated.
    struct DirectCoinTransferConfigUpdatedEvent has drop, store {
        new_allow_direct_transfers: bool
    }
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L179-219)
```text
    public fun assert_account_is_registered_for_apt(addr: address) {
        assert_account_exists(addr);






























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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-870)
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
```
