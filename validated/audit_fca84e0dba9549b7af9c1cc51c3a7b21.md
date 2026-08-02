### Title
Unregistered/opted-out shareholder deposit aborts `vesting::distribute`, permanently blocking payouts to all shareholders in the pool - (File: aptos-move/framework/aptos-framework/sources/vesting.move)

### Summary
`vesting::distribute` iterates over every shareholder of a vesting contract in a single transaction and calls `aptos_account::deposit_coins` to pay each one. `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has not registered a `CoinStore<AptosCoin>` and has opted out of direct coin transfers via `aptos_account::set_allow_direct_coin_transfers(false)`. Because the loop pays all shareholders atomically, a single unregistered/opted-out recipient causes the entire `distribute` call to revert, blocking payouts to every other (unrelated) shareholder in the same vesting pool. This is the direct Move analog of the reported Chainlink "unhandled revert locks all access" bug class: one address's state can brick a shared multi-user call path with no fallback.

### Finding Description
`vesting::create_vesting_contract` verifies `withdrawal_address` is registered for APT via `assert_account_is_registered_for_apt` [1](#0-0) , and `set_beneficiary` similarly requires the *new* beneficiary to be registered before it can be set, explicitly to avoid breaking `distribute()` for everyone else [2](#0-1) .

However, `create_vesting_contract`'s `shareholders` list is never checked for APT registration [3](#0-2) , and `reset_beneficiary` (which redirects distributions back to the raw shareholder address) also performs no registration check before removing the beneficiary mapping [4](#0-3) .

`distribute` then pays every shareholder in one atomic loop: [5](#0-4) 

`aptos_account::deposit_coins` only auto-registers a `CoinStore` for a recipient that has not opted out; if the recipient explicitly disabled direct coin transfers (`can_receive_direct_coin_transfers(to) == false`) and has no existing `CoinStore<AptosCoin>`, the call aborts: [6](#0-5) 

Since `set_allow_direct_coin_transfers` is a public entry function any account can call on itself at any time (unprivileged), a shareholder (or a beneficiary who is later reset back to an unregistered shareholder via `reset_beneficiary`) can put their own address into this opted-out, unregistered state. The next call to `distribute()` for that vesting contract will then abort for *all* shareholders, not just the one who opted out, because the failing `deposit_coins` call reverts the whole transaction.

### Impact Explanation
This breaks the "distribution/withdrawal rights must not be strandable" invariant for vesting flows. All shareholders in the affected vesting contract lose the ability to receive their vested distributions (including rewards and commission-adjusted payouts) via `distribute()`/`vest()`-triggered flows, until the misconfigured shareholder either re-enables direct transfers or manually registers a `CoinStore<AptosCoin>`. Because any single shareholder can trigger this state (intentionally or accidentally) and it silently blocks unrelated shareholders' claim rights, this is a legitimate value-locking/DoS impact on a mainnet-relevant vesting flow, matching the "Permanent lock or non-recoverable loss of claim rights ... in vesting flows" impact category. It's not fund-theft, and the lock is technically recoverable (once the offending account re-registers), which caps this below a full "permanent" loss but still constitutes a broad denial of legitimate withdrawal rights for co-shareholders who did nothing wrong.

### Likelihood Explanation
High likelihood: `set_allow_direct_coin_transfers(false)` is a normal, unprivileged, publicly documented account-configuration entry function; no special permissions or preconditions are needed to flip a shareholder's own account into the failing state. Multi-shareholder vesting contracts are a standard use case (validator/employee vesting pools created in `genesis.move`), so the attack surface (any co-shareholder self-DoS'ing the whole pool) is realistic and requires no cooperation from the admin.

### Recommendation
- In `create_vesting_contract`, require all `shareholders` to be registered for APT (mirroring the existing `assert_account_is_registered_for_apt` check already applied to `withdrawal_address` and to `new_beneficiary` in `set_beneficiary`).
- In `reset_beneficiary`, require the underlying shareholder address to be registered for APT before allowing the beneficiary mapping to be removed, or otherwise re-validate at reset time.
- In `distribute`, make individual per-shareholder payouts resilient to a single failure (e.g., skip/queue the failed recipient's share instead of reverting the whole batch), so one bad recipient cannot block payouts to the rest of the pool.

### Proof of Concept
1. Admin creates a vesting contract with shareholders A and B via `create_vesting_contract` (`vesting.move` L537-559); no APT-registration check is performed on A or B.
2. Shareholder A calls the unprivileged `aptos_account::set_allow_direct_coin_transfers(false)` on their own account and never registers a `CoinStore<AptosCoin>` (or, alternatively, admin calls `set_beneficiary` to point A's payouts at a beneficiary and later `reset_beneficiary` resets it back to A's raw, unregistered/opted-out address).
3. Vesting periods elapse; `vest()` is called, moving vested stake into a withdrawable state (`vesting.move` L654-704).
4. Anyone calls `distribute(contract_address)` (`vesting.move` L719-751). The loop reaches A's `aptos_account::deposit_coins` call, which hits `can_receive_direct_coin_transfers(A) == false` and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` (`aptos_account.move` L111-131).
5. The entire `distribute` transaction reverts, so shareholder B (and any other shareholders) also fail to receive their already-vested distributions, even though they did nothing wrong.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L536-559)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L950-967)
```text
    public entry fun reset_beneficiary(
        account: &signer,
        contract_address: address,
        shareholder: address,
    ) acquires VestingAccountManagement, VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let addr = signer::address_of(account);
        assert!(
            addr == vesting_contract.admin ||
                addr == get_role_holder(contract_address, utf8(ROLE_BENEFICIARY_RESETTER)),
            error::permission_denied(EPERMISSION_DENIED),
        );

        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            beneficiaries.remove(&shareholder);
        };
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
