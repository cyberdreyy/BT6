## Title
Permanent denial-of-service of `vesting::distribute()` via a single shareholder blocking coins for all other vesting shareholders - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`vesting::distribute()` iterates over **all** shareholders of a vesting contract's `grant_pool` in a single loop and calls `aptos_account::deposit_coins` for each recipient [1](#0-0) . `aptos_account::deposit_coins` aborts if the recipient has previously called `set_allow_direct_coin_transfers(false)` on their own account and has not registered a `CoinStore<AptosCoin>` [2](#0-1) . Because Move transactions are atomic, one poisoned recipient inside the loop reverts the *entire* `distribute()` call — including the coin withdrawal already performed from the stake pool — every time it is invoked, permanently blocking distributions to every other (honest) shareholder of that vesting contract. This is the exact bug class from the external report: a single malicious/irrecoverable entry inside an unskippable payout loop blocks payout to everyone queued after (or alongside) it.

### Finding Description
`distribute()` is a permissionless entry function — any account may call it for any `contract_address` [3](#0-2) . It withdraws all currently-withdrawable stake and then iterates the vesting contract's fixed shareholder set, computing each shareholder's pro-rata share and calling `aptos_account::deposit_coins(recipient_address, share_of_coins)`:

```
730|        // Distribute coins to all shareholders in the vesting contract.
731|        let grant_pool = &vesting_contract.grant_pool;
732|        let shareholders = &grant_pool.shareholders();
733|        shareholders.for_each_ref(|shareholder| {
...
738|            let recipient_address = get_beneficiary(vesting_contract, shareholder);
739|            aptos_account::deposit_coins(recipient_address, share_of_coins);
740|        });
``` [1](#0-0) 

`aptos_account::deposit_coins` will `assert!` and abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is not yet registered for the coin type and has explicitly opted out of unsolicited direct transfers:
```
111|    public fun deposit_coins<CoinType>(
112|        to: address, coins: Coin<CoinType>
113|    ) acquires DirectTransferConfig {
114|        if (!account::exists_at(to)) {
115|            create_account(to);
...
123|        if (!coin::is_account_registered<CoinType>(to)) {
124|            assert!(
125|                can_receive_direct_coin_transfers(to),
126|                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
127|            );
128|            coin::register<CoinType>(&create_signer(to));
129|        };
130|        coin::deposit<CoinType>(to, coins)
131|    }
``` [2](#0-1) 

Any account can flip this flag to `false` for itself via the standard, unprivileged entry function `set_allow_direct_coin_transfers` [4](#0-3) , and simply never call `coin::register<AptosCoin>` for itself afterward.

A shareholder's grant amount and address are fixed at vesting-contract creation via `create_vesting_contract`, which builds the `grant_pool` from the caller-supplied `shareholders`/`buy_ins` vectors [5](#0-4) ; there is no exposed function in `vesting.move` to remove or replace a shareholder from `grant_pool` afterward — only `set_beneficiary`/`reset_beneficiary` (admin-only) redirect that shareholder's payout target, they do not remove the shareholder entry itself. Since the recipient the loop actually pays is `get_beneficiary(vesting_contract, shareholder)` (defaults to the shareholder's own address when no beneficiary is set), the shareholder fully controls the vulnerable recipient address by default.

Because `distribute()` performs the stake withdrawal and the entire payout loop within one atomic transaction, if it aborts partway through the shareholder loop, **all** effects (the stake withdrawal, and payouts to shareholders enumerated before the poisoned one) are rolled back. Since the poisoned shareholder's state (no CoinStore + direct transfers disabled) persists indefinitely and is fully under that shareholder's control, every future call to `distribute()` will deterministically fail at the same point, permanently denying payouts to every other shareholder in that vesting contract. The framework's own Move spec acknowledges this gap explicitly: `spec distribute(...) { // TODO: Can't handle abort in loop. pragma verify = false; ... }` [6](#0-5) .

The identical unskippable-loop pattern also exists in `staking_contract::distribute_internal`, which pays out `distribution_pool` shareholders (staker + operator/beneficiary) via `aptos_account::deposit_coins` inside a `while` loop [7](#0-6) , but that pool only ever contains the staker and the operator's beneficiary, giving no unprivileged multi-party attack surface analogous to vesting's arbitrary shareholder list.

### Impact Explanation
Once one shareholder in a `vesting::VestingContract` poisons their receiving address (self-inflicted, requires no special privilege), `distribute()` becomes permanently unusable for that contract. All other shareholders — who did nothing wrong — can never again receive their unlocked rewards or vested grant tranches through `distribute()`; the withdrawable stake sits stuck in the stake pool/vesting flow indefinitely. This is a permanent, non-recoverable loss of claim rights for the vesting flow's other stakeholders and matches the required impact "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows."

### Likelihood Explanation
Likelihood is high for any multi-shareholder vesting contract with a shareholder who is (or becomes) hostile or careless: the only actions required are two standard, permissionless calls on the attacker's own account — `set_allow_direct_coin_transfers(account, false)` and simply not calling `coin::register<AptosCoin>`. No coordination with the admin, operator, or other shareholders is needed, and the condition is durable across epochs/lockup cycles since nothing in `vesting.move` forces re-registration or purges the shareholder.

### Recommendation
`distribute()` (and analogously `staking_contract::distribute_internal`) should not let a single recipient's failed deposit abort the whole distribution. Wrap the per-shareholder `aptos_account::deposit_coins` call so failures are caught/skipped (e.g., check `coin::is_account_registered` / `can_receive_direct_coin_transfers` before depositing and, if the recipient cannot currently receive funds, retain their shares in the pool or route the pending amount to a claimable/escrow mechanism instead of aborting the entire transaction).

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[Alice, Bob]` via `create_vesting_contract`.
2. Bob (an ordinary shareholder, no special privilege) calls `aptos_account::set_allow_direct_coin_transfers(bob, false)` and never calls `coin::register<AptosCoin>(bob)`.
3. Time passes; rewards/vested tokens accumulate and become withdrawable.
4. Anyone calls `vesting::distribute(contract_address)`. The loop reaches Bob, `aptos_account::deposit_coins(bob, ...)` hits `assert!(can_receive_direct_coin_transfers(bob), ...)` and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The whole transaction reverts — Alice's share, already computed and about to be paid in the same transaction, is rolled back along with the stake withdrawal.
6. Every subsequent call to `distribute()` fails identically, permanently, since Bob's flag/registration state never changes on its own.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L560-576)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L719-729)
```text
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L188-211)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L307-314)
```text
    spec distribute(contract_address: address) {
        // TODO: Can't handle abort in loop.
        pragma verify = false;
        include ActiveVestingContractAbortsIf;

        let vesting_contract = global<VestingContract>(contract_address);
        include WithdrawStakeAbortsIf { vesting_contract };
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
