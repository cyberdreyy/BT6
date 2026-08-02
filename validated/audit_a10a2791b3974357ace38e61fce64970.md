### Title
Permanent denial of vesting fund distribution via a shareholder disabling direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
The predeployed WETH9 bug lets one recipient's revert-prone receive path block that user's own withdrawal. The Aptos-native analog is stronger: in `vesting::distribute`, a single unprivileged shareholder can permanently block fund distribution to *all other* shareholders of the same vesting contract by disabling direct coin transfers on their own account, causing `aptos_account::deposit_coins` to abort inside a shared, unguarded loop.

### Finding Description
`vesting::distribute` iterates over every shareholder of a `VestingContract` and unconditionally calls `aptos_account::deposit_coins` for each one in a single atomic loop: [1](#0-0) 

`aptos_account::deposit_coins` only auto-registers a `CoinStore` for the recipient if `can_receive_direct_coin_transfers` is true; otherwise it aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`: [2](#0-1) 

Any account can call the standard, unprivileged `account::set_allow_direct_coin_transfers(false)` on itself without ever registering an `AptosCoin` `CoinStore`. `create_vesting_contract` never validates that shareholders are registered for APT or have direct transfers enabled — that check (`assert_account_is_registered_for_apt`) is only applied to the `withdrawal_address` and to a beneficiary explicitly set later via `set_beneficiary`: [3](#0-2) [4](#0-3) 

Because Move has no isolated/try-catch execution, if any single shareholder (or their default receiving address, since an unset beneficiary defaults to the shareholder's own address) becomes unable to accept a direct coin transfer, `distribute()` aborts for the *entire* transaction — not just that shareholder's share. Since `distribute` is a permissionless entry function callable by anyone, and vesting contracts pool multiple shareholders' claims together (`grant_pool`), this stalls the whole pool's reward/vested-token distribution indefinitely, until either the offending shareholder re-enables transfers or the admin does an out-of-band accommodation. The admin has no mechanism to skip or force through the payout for the non-blocking shareholders inside `distribute`.

### Impact Explanation
This traps stake/vesting value that legitimately belongs to other, uninvolved shareholders — matching the "Permanent lock or non-recoverable loss of claim rights in ... vesting flows" and "corruption that ... traps value" impact categories. A single delinquent or malicious shareholder in a multi-shareholder vesting contract can indefinitely deny all co-shareholders their vested/reward APT, since `unlock_rewards`/`vest` still accrue distributable balances into the shared stake pool but `distribute()` (the only path to actually move funds out) can never succeed while the blocking condition persists.

### Likelihood Explanation
The precondition is trivial and requires no special privilege: any shareholder address can invoke the standard `account::set_allow_direct_coin_transfers(false)` entry function on their own account without ever registering a `CoinStore`. Multi-shareholder vesting contracts (`create_vesting_contract` explicitly supports a list of `shareholders`) are a normal, expected configuration in this module, so the attack surface (a co-shareholder being or becoming malicious/misconfigured) is realistic and requires only one non-cooperating participant.

### Recommendation
- In `create_vesting_contract`, require every shareholder address to be registered for `AptosCoin` (or have direct transfers enabled) before accepting them into the pool, analogous to the existing `assert_account_is_registered_for_apt(withdrawal_address)` check.
- In `distribute`, make individual recipient payout failures non-blocking for the rest of the pool — e.g., skip/accrue an un-deliverable share to a per-shareholder pending balance (claimable later) instead of aborting the whole loop, or use a push/pull pattern where each shareholder withdraws their own share independently rather than a single shared iteration transferring to everyone.

### Proof of Concept
1. Admin creates a vesting contract with two shareholders, `S1` and `S2`, via `create_vesting_contract` (no registration check is performed on shareholders).
2. `S1` calls `account::set_allow_direct_coin_transfers(false)` on their own account and never registers an `AptosCoin` `CoinStore`.
3. Time passes; `vest`/`unlock_rewards` are called, accumulating a withdrawable balance in the underlying stake pool for both `S1` and `S2`.
4. Anyone calls `vesting::distribute(contract_address)`. The loop reaches `S1`, calls `aptos_account::deposit_coins(S1, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` because `S1` has no `CoinStore` and disabled direct transfers.
5. The whole transaction reverts — `S2` (and all future distributions) can never receive their vested/reward APT as long as `S1` keeps this configuration, permanently freezing `S2`'s legitimate claim.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L536-576)
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

        // Create a coins pool to track shareholders and shares of the grant.
        let grant = coin::zero<AptosCoin>();
        let grant_amount = 0;
        let grant_pool = pool_u64::create(MAXIMUM_SHAREHOLDERS);
        shareholders.for_each_ref(|shareholder| {
            let shareholder: address = *shareholder;
            let (_, buy_in) = simple_map::remove(&mut buy_ins, &shareholder);
            let buy_in_amount = coin::value(&buy_in);
            coin::merge(&mut grant, buy_in);
            pool_u64::buy_in(
                &mut grant_pool,
                shareholder,
                buy_in_amount,
            );
            grant_amount += buy_in_amount;
        });
        assert!(grant_amount > 0, error::invalid_argument(EZERO_GRANT));
```

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-936)
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
