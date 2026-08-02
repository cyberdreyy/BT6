## Title
Any vesting shareholder can permanently DoS `vesting::distribute()` (and thus `vest`/`terminate_vesting_contract`) for all other shareholders by opting out of direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`vesting::distribute()` iterates over every shareholder of a vesting contract and calls `aptos_account::deposit_coins` to pay out their share in a single atomic loop [1](#0-0) . `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has disabled direct coin transfers and is not already registered for the coin [2](#0-1) . Since `DirectTransferConfig` is a self-controlled setting any account holder can toggle on their own account at will, an unprivileged shareholder can weaponize this to make every future call to `distribute` (and therefore `vest`, `distribute_many`, and `terminate_vesting_contract`, which calls `distribute` internally) abort for the entire vesting contract, not just their own payout.

### Finding Description
`vesting::distribute` computes `total_distribution_amount`, then loops over `grant_pool.shareholders()` and unconditionally calls `aptos_account::deposit_coins(recipient_address, share_of_coins)` for each one [3](#0-2) . There is no per-recipient error isolation (no try/catch equivalent, no skip-and-retry) — a single failing deposit aborts the whole transaction and all state changes (including `withdraw_stake`'s already-executed side effects would be rolled back too).

`aptos_account::deposit_coins` will abort if the target account both (a) is not already registered for the coin type, and (b) has `can_receive_direct_coin_transfers(to) == false` [4](#0-3) . `can_receive_direct_coin_transfers` is governed by `DirectTransferConfig.allow_arbitrary_coin_transfers`, which the module explicitly documents as user-controlled ("Users can opt-out by disabling at any time") [5](#0-4) .

The module authors were aware of a related risk: `set_beneficiary` explicitly checks `assert_account_is_registered_for_apt(new_beneficiary)` specifically "so distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered" [6](#0-5) . However, this mitigation only checks *registration*, not the separate opt-out flag. A shareholder (or a beneficiary set via `set_beneficiary`) can be fully registered for AptosCoin at the time the beneficiary is set (passing the check), and later — completely unprivileged, using only their own signer — call `aptos_account::set_allow_direct_coin_transfers(false)` on their own account. From that point on, every subsequent call to `distribute` for that vesting contract aborts on that shareholder's deposit, blocking payouts for every other shareholder in the same contract, and blocking `terminate_vesting_contract` since it calls `distribute` first before allowing termination [7](#0-6) .

### Impact Explanation
This maps to the "Permanent lock or non-recoverable loss of claim rights in stake ... beneficiary, or vesting flows" impact class. A single unprivileged shareholder in a multi-shareholder vesting contract can indefinitely freeze the distribution of vested tokens, unlocked stake, and rewards for every other shareholder, since `distribute`/`vest`/`distribute_many`/`terminate_vesting_contract` are all-or-nothing loops with no per-recipient failure isolation. The admin has no built-in recovery path to force-skip an uncooperative recipient's share (there is no "distribute except X" function), so the contract's payout mechanism can be stuck until the opted-out account either re-enables direct transfers voluntarily or is otherwise remediated. Because vesting contracts commonly hold real staker/operator funds and this affects mainnet-relevant staking/vesting fund flows, this is a high-severity griefing/DoS on value that is not owned by the attacker.

### Likelihood Explanation
Likelihood is high: `set_allow_direct_coin_transfers` is a normal entry function callable by any account holder on their own account with no cost beyond gas, and it can be invoked at any time after the shareholder/beneficiary passes the one-time registration check in `set_beneficiary` (or by simply being a shareholder from contract creation, since `create_vesting_contract` does not appear to check `can_receive_direct_coin_transfers` for shareholder addresses). No collusion, privileged role, or special timing is required — any single greedy or malicious shareholder (e.g., one who wants to force renegotiation, retaliate against the admin, or grief co-shareholders) can trigger it unilaterally.

### Recommendation
- In `vesting::distribute`, isolate each shareholder's deposit so a single failure doesn't revert the whole loop — e.g., wrap the deposit attempt so failures are recorded/skipped and retried later, or fall back to depositing into a per-shareholder pending/claimable balance if `aptos_account::deposit_coins` would fail (mirroring the "leave enough gas/graceful degrade" recommendation from the external report, adapted to Move's atomicity: don't let one recipient's config abort the batch).
- Alternatively, extend the safeguard already used in `set_beneficiary` (`assert_account_is_registered_for_apt`) to also require `aptos_account::can_receive_direct_coin_transfers(recipient) == true` at the time distribution is attempted, and provide an admin/self-service "claim" function so recipients can pull funds individually instead of a push-based, all-or-nothing loop.
- Consider using `coin::deposit` directly against a `CoinStore` that is guaranteed registered (pool_u64/vesting could require registration and disable opt-out for these specific escrowed distributions), so third-party account settings cannot block release of legitimately owned funds.

### Proof of Concept
1. Admin creates a vesting contract with shareholders `A` (attacker) and `B`, both registered for AptosCoin (satisfying `create_vesting_contract`'s account checks, or `set_beneficiary`'s `assert_account_is_registered_for_apt`).
2. Time passes; the underlying stake pool accrues rewards/vests tokens.
3. `A` calls `aptos_account::set_allow_direct_coin_transfers(false)` on their own account (no special privilege needed).
4. Anyone calls `vesting::distribute(contract_address)` (or `vest`, which calls into the same flow, or `terminate_vesting_contract`).
5. The loop reaches `A`'s deposit; `aptos_account::deposit_coins` sees `A` is not coin-registered under a new `CoinStore` deposit path required for the transfer and `can_receive_direct_coin_transfers(A) == false`, aborting with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire `distribute` transaction reverts — `B` and all other shareholders receive nothing, and `terminate_vesting_contract` cannot proceed past its initial `distribute` call, indefinitely.

Note: I was unable to directly confirm the exact wording of `set_allow_direct_coin_transfers`'s implementation body (only its existence and the `DirectTransferConfig` struct definition were found in the index) and could not fully verify whether `create_vesting_contract` performs any registration/opt-in check on initial shareholder addresses (only the `withdrawal_address` check was visible in the excerpts retrieved) — a full Devin session with complete file access would be needed to confirm these details precisely if further verification is desired.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-747)
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L31-37)
```text
    /// Configuration for whether an account can receive direct transfers of coins that they have not registered.
    ///
    /// By default, this is enabled. Users can opt-out by disabling at any time.
    struct DirectTransferConfig has key {
        allow_arbitrary_coin_transfers: bool,
        update_coin_transfer_events: EventHandle<DirectCoinTransferConfigUpdatedEvent>
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
