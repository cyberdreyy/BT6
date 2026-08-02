## Finding: Single-recipient revert can permanently block `vesting::distribute()` for all other shareholders

### Title
Denial-of-service / value lock in `vesting::distribute()` via revertible `aptos_account::deposit_coins` batch payout - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
The Move-native analog to the Solidity `transfer()`-revert bug is `aptos_account::deposit_coins`, which can abort mid-transaction if the recipient is not registered for the coin and has not opted in to direct coin transfers [1](#0-0) . `vesting::distribute()` calls this function inside a loop over *all* shareholders of a vesting contract in a single atomic transaction [2](#0-1) . If any one shareholder's beneficiary address cannot accept the deposit, the entire `distribute()` call aborts, blocking payout to every other, unrelated shareholder in the same vesting pool.

### Finding Description
`distribute()` withdraws all currently-inactive stake from the pool as a single `Coin<AptosCoin>` and then iterates the vesting contract's `grant_pool` shareholders, computing each one's share and depositing it to `get_beneficiary(vesting_contract, shareholder)`: [3](#0-2) 

The deposit path used, `aptos_account::deposit_coins`, requires the recipient to already be registered for `AptosCoin`, or else it checks `can_receive_direct_coin_transfers(to)` and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if that flag is false (the default state for most accounts) [1](#0-0) .

Unlike `set_beneficiary()`, which explicitly requires the *new* beneficiary to already be `assert_account_is_registered_for_apt` before being accepted [4](#0-3) , the initial shareholder list supplied to `create_vesting_contract()` is never checked for APT registration — only the `withdrawal_address` is validated [5](#0-4) . A shareholder can therefore start (or later become, e.g. by disabling `set_allow_direct_coin_transfers`) unregistered/non-accepting, causing every future `distribute()` call for that vesting contract to revert for *all* shareholders, not just the offending one.

This mirrors the external report's root cause exactly: a hard, unconditional external call/deposit inside a batched payout function whose failure mode is a full revert rather than isolated failure, letting one uncooperative or malicious party grief every other legitimate claimant.

### Impact Explanation
Every shareholder sharing a vesting contract with a non-accepting recipient loses the ability to ever receive their vested APT via `distribute()` or `distribute_many()` (both revert) until the blocking account becomes registered/opt-in — something outside the control of the admin or the other shareholders. This is a value-in-limbo (temporary-to-permanent stranding) condition on vesting balances, matching the "Permanent lock or non-recoverable loss of claim rights ... in vesting flows" impact class. `terminate_vesting_contract()` also calls `distribute()` first [6](#0-5) , so contract termination/admin withdrawal is blocked as well.

### Likelihood Explanation
Any of the shareholders configured on a vesting contract (or an account they later revoke direct-transfer permission on) can trigger this without any special privilege — they only need to be one of the addresses in the shareholder list passed at contract creation, which is a normal, expected role. No governance or admin compromise is required.

### Recommendation
- Validate at `create_vesting_contract()` time that every shareholder address is registered for `AptosCoin` (mirroring the check already done for `withdrawal_address` and for `set_beneficiary`'s `new_beneficiary`).
- In `distribute()`, isolate each shareholder's deposit (e.g., via a "best effort" pattern that skips/holds back an individual failed deposit into an escrow the shareholder can later claim) instead of letting one failure abort the whole batch.

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[A, B]` via `create_vesting_contract`, where `B` is a freshly created account that has never registered for `AptosCoin` and has not called `set_allow_direct_coin_transfers(true)`.
2. Vesting period elapses; `vest()` and `unlock_rewards()` are called normally, accruing a payable balance.
3. Anyone calls `distribute(contract_address)`. In the shareholder loop, when reaching `B`, `aptos_account::deposit_coins(B, ...)` hits `coin::is_account_registered<AptosCoin>(B) == false` and `can_receive_direct_coin_transfers(B) == false`, aborting with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
4. The whole transaction reverts — `A`'s share (already computed and about to be paid) is not distributed either, and `terminate_vesting_contract` (which also calls `distribute` first) is likewise blocked.

Note: I was not able to fully trace the interaction between `coin::is_account_registered<AptosCoin>` and the ongoing Fungible-Asset migration for `AptosCoin` in this index (the `deposit_coins`/`coin::is_account_registered` implementation body was only partially visible), so there is residual uncertainty about whether AptosCoin-specific auto-registration behavior fully bypasses this check in the current production configuration. This should be verified against the live `coin.move` / `fungible_asset` migration logic before treating this as a confirmed, exploitable bug.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L536-558)
```text
    /// Create a vesting contract with a given configurations.
    public fun create_vesting_contract(
        admin: &signer,
        shareholders: &vector<address>,
        buy_ins: SimpleMap<address, Coin<AptosCoin>>,
        vesting_schedule: VestingSchedule,
        withdrawal_address: address,
        operator: address,
        voter: address,
        commission_percentage: u64,
        // Optional seed used when creating the staking contract account.
        contract_creation_seed: vector<u8>,
    ): address acquires AdminStore {
        assert!(
            !system_addresses::is_reserved_address(withdrawal_address),
            error::invalid_argument(EINVALID_WITHDRAWAL_ADDRESS),
        );
        assert_account_is_registered_for_apt(withdrawal_address);
        assert!(shareholders.length() > 0, error::invalid_argument(ENO_SHAREHOLDERS));
        assert!(
            buy_ins.length() == shareholders.length(),
            error::invalid_argument(ESHARES_LENGTH_MISMATCH),
        );
```

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-775)
```text
    /// Terminate the vesting contract and send all funds back to the withdrawal address.
    public entry fun terminate_vesting_contract(admin: &signer, contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        // Distribute all withdrawable coins, which should have been from previous rewards withdrawal or vest.
        distribute(contract_address);
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
