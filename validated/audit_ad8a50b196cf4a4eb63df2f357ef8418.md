Based on the investigation, I found a strong stake/lockup analog to the BondAggregator gas-limit bug in `staking_contract.move`'s commission/reward distribution loop.

### Title
Unbounded on-chain loop in `distribute_internal` over `distribution_pool` shareholders can permanently strand staker rewards and operator commission - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`distribute_internal` iterates over every shareholder currently registered in a `StakingContract`'s `distribution_pool` inside a single, non-paginated `while` loop, performing a `shares()` read, a `redeem_shares()` write, a `beneficiary_for_operator` lookup, a coin deposit, and an event emission per iteration [1](#0-0) . This is structurally identical to `BondAggregator.liveMarketsBy`'s for-loop over `marketCounter`: it has no start/stop bound and its cost grows linearly with the number of distinct recipients ever added to the pool via `add_distribution` [2](#0-1) .

### Finding Description
`add_distribution` accepts an arbitrary `recipient` address and buys that address shares in the `StakingContract`'s `distribution_pool` [2](#0-1) . The pool is a `pool_u64::Pool`, whose `shareholders` vector only grows on `add_shares`/`buy_in` and shrinks only when a shareholder's shares are fully redeemed to zero, which is exactly what `distribute_internal`'s loop does one shareholder at a time [3](#0-2) , [4](#0-3) .

Distribution recipients are not limited to just "staker" and "operator": `request_commission_internal`/`add_distribution` record the *operator address at the time of the call* as a distribution recipient, and this is invoked as part of the `switch_operator` flow (referenced by `staking_proxy::set_operator` and covered by `test_set_operator`) before the `StakingContract`'s operator key is updated [5](#0-4) . Because the same underlying `StakingContract` (and its single `distribution_pool`) persists across operator switches, each switch that leaves an un-redeemed commission balance for the outgoing operator adds a new, permanent line item to the pool's `shareholders` vector. Repeating this (staker legitimately/unprivileged calls `switch_operator` many times before ever calling `distribute`) can grow the shareholder count arbitrarily, since nothing in `add_distribution` enforces a cap tied to gas cost (only `pool_u64`'s generic `shareholders_limit` bounds it, and `vesting`'s grant pool is separately capped via `MAXIMUM_SHAREHOLDERS` [6](#0-5) , but there is no equivalent low bound demonstrated for the `staking_contract` distribution pool in the code reviewed).

Every future call to `distribute`, `request_commission`, or `unlock_rewards` — all of which call `distribute_internal` — must fully drain this pool in one transaction [7](#0-6) , [8](#0-7) . Once the shareholder count is large enough that the loop's cumulative gas exceeds Aptos's per-transaction max gas, `distribute_internal` will deterministically abort every time it's invoked, since Move's `while` loop cannot be partially executed and resumed across transactions.

### Impact Explanation
Once the pool is large enough to exceed the max gas budget, `distribute`, `request_commission`, and `unlock_rewards` become permanently unusable for that staking contract. This traps the staker's already-unlocked rewards and the operator's/beneficiary's already-unlocked commission inside the stake pool's `Store` resource indefinitely — a non-recoverable loss of claim rights matching "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows," and any subsequent commission accounting via `update_distribution_pool`/`add_distribution` would also fail since it too routes through the same distribution pool state, corrupting further share accounting for legitimate parties.

### Likelihood Explanation
The trigger path (repeated `switch_operator` calls without draining the distribution pool via `distribute` in between) is reachable by the staker alone using only entry functions already exposed (`staking_contract::update_commision`/`staking_proxy::set_operator` family), with no privileged role required beyond being the pool's own staker — moderate likelihood, gated mainly by the number of operator switches needed to reach the gas ceiling, which was not empirically determined here.

### Recommendation
Add pagination (start/stop index or bounded batch size per call) to `distribute_internal`'s draining loop, analogous to the BondAggregator fix, and/or prune or merge duplicate/stale recipient entries from `distribution_pool` on `switch_operator` so its `shareholders` vector cannot grow unbounded from repeated operator changes.

### Proof of Concept
Conceptual, not executed against a running node:
1. Staker creates a `staking_contract` with `operator_1`, contract accrues rewards/commission.
2. Staker repeatedly calls `switch_operator`/`switch_operator_with_same_commission` (via `staking_contract`/`staking_proxy`) through many distinct operator addresses without calling `distribute` in between, so each old operator's un-redeemed commission share is added as a new `distribution_pool` shareholder each time (per `add_distribution` at [2](#0-1) ).
3. After enough switches, call `distribute` — `distribute_internal`'s `while` loop [1](#0-0)  must redeem every accumulated shareholder in one transaction and exceeds the max gas limit, aborting permanently.

**Caveat**: I could not directly inspect the full body of `switch_operator`/`switch_operator_with_same_commission` in this session (index did not surface its full source), so the exact mechanics of how/whether an old operator's un-redeemed commission is left as a standing `distribution_pool` entry across switches is inferred from `add_distribution`'s generic recipient parameter and `staking_proxy`'s reference to `set_operator`, not fully confirmed line-by-line. A Devin session with full repo access should verify `switch_operator`'s exact recipient-handling logic before treating this as fully proven.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L603-629)
```text
    /// Unlock commission amount from the stake pool. Operator needs to wait for the amount to become withdrawable
    /// at the end of the stake pool's lockup period before they can actually can withdraw_commission.
    ///
    /// Only staker, operator or beneficiary can call this.
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L133-169)
```text
    /// Allow an existing or new shareholder to add their coins to the pool in exchange for new shares.
    public fun buy_in(self: &mut Pool, shareholder: address, coins_amount: u64): u64 {
        if (coins_amount == 0) return 0;

        let new_shares = self.amount_to_shares(coins_amount);
        assert!(MAX_U64 - self.total_coins >= coins_amount, error::invalid_argument(EPOOL_TOTAL_COINS_OVERFLOW));
        assert!(MAX_U64 - self.total_shares >= new_shares, error::invalid_argument(EPOOL_TOTAL_COINS_OVERFLOW));

        self.total_coins += coins_amount;
        self.total_shares += new_shares;
        self.add_shares(shareholder, new_shares);
        new_shares
    }

    /// Add the number of shares directly for `shareholder` in `self`.
    /// This would dilute other shareholders if the pool's balance of coins didn't change.
    fun add_shares(self: &mut Pool, shareholder: address, new_shares: u64): u64 {
        if (self.contains(shareholder)) {
            let existing_shares = self.shares.borrow_mut(&shareholder);
            let current_shares = *existing_shares;
            assert!(MAX_U64 - current_shares >= new_shares, error::invalid_argument(ESHAREHOLDER_SHARES_OVERFLOW));

            *existing_shares = current_shares + new_shares;
            *existing_shares
        } else if (new_shares > 0) {
            assert!(
                self.shareholders.length() < self.shareholders_limit,
                error::invalid_state(ETOO_MANY_SHAREHOLDERS),
            );

            self.shareholders.push_back(shareholder);
            self.shares.add(shareholder, new_shares);
            new_shares
        } else {
            new_shares
        }
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L171-180)
```text
    /// Allow `shareholder` to redeem their shares in `self` for coins.
    public fun redeem_shares(self: &mut Pool, shareholder: address, shares_to_redeem: u64): u64 {
        assert!(self.contains(shareholder), error::invalid_argument(ESHAREHOLDER_NOT_FOUND));
        assert!(self.shares(shareholder) >= shares_to_redeem, error::invalid_argument(EINSUFFICIENT_SHARES));

        if (shares_to_redeem == 0) return 0;

        let redeemed_coins = self.shares_to_amount(shares_to_redeem);
        self.total_coins -= redeemed_coins;
        self.total_shares -= shares_to_redeem;
```

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L85-120)
```text
    #[test(
        aptos_framework = @0x1,
        owner = @0x123,
        operator_1 = @0x234,
        operator_2 = @0x345,
        new_operator = @0x567,
    )]
    public entry fun test_set_operator(
        aptos_framework: &signer,
        owner: &signer,
        operator_1: &signer,
        operator_2: &signer,
        new_operator: &signer,
    ) {
        let owner_address = signer::address_of(owner);
        let operator_1_address = signer::address_of(operator_1);
        let operator_2_address = signer::address_of(operator_2);
        let new_operator_address = signer::address_of(new_operator);
        vesting::setup(
            aptos_framework, &vector[owner_address, operator_1_address, operator_2_address, new_operator_address]);
        staking_contract::setup_staking_contract(aptos_framework, owner, operator_1, INITIAL_BALANCE, 0);
        staking_contract::setup_staking_contract(aptos_framework, owner, operator_2, INITIAL_BALANCE, 0);

        let vesting_contract_1 = vesting::setup_vesting_contract(owner, &vector[@11], &vector[INITIAL_BALANCE], owner_address, 0);
        vesting::update_operator(owner, vesting_contract_1, operator_1_address, 0);
        let vesting_contract_2 = vesting::setup_vesting_contract(owner, &vector[@12], &vector[INITIAL_BALANCE], owner_address, 0);
        vesting::update_operator(owner, vesting_contract_2, operator_2_address, 0);

        let (_sk, pk, pop) = stake::generate_identity();
        stake::initialize_test_validator(&pk, &pop, owner, INITIAL_BALANCE, false, false);
        stake::set_operator(owner, operator_1_address);

        set_operator(owner, operator_1_address, new_operator_address);
        // Stake pool's operator has been switched from operator 1 to new operator.
        assert!(stake::get_operator(owner_address) == new_operator_address, 0);
        // Staking contract has been switched from operator 1 to new operator.
```

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
