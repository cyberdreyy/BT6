### Title
Front-running of `create_staking_pool()` allows an unprivileged attacker to squat a staking-pool account ID and become its owner, diverting future reward fees - (File: `staking-pool-factory/src/lib.rs`)

### Summary
`StakingPoolFactory::create_staking_pool()` is permissionless and lets any caller choose the `staking_pool_id` prefix that becomes part of the resulting pool's account ID (`{staking_pool_id}.{factory}`), set themselves as `owner_id`, and set an arbitrary `reward_fee_fraction`. The only guard is that the derived account ID must not already exist. This mirrors the reported pattern in `RocketJoeFactory.createRJLaunchEvent()`, where a cheap, permissionless call that binds a scarce identifier (there, `_token`; here, the pool's account-ID namespace) to whatever arguments the caller supplies lets an attacker seize the binding before the rightful party and then benefit from that seized position.

### Finding Description
`create_staking_pool` requires only the minimum attached deposit (`MIN_ATTACHED_BALANCE`, 30 NEAR) and performs no check that the caller is authorized to use a particular `staking_pool_id`: [1](#0-0) 

Once the account ID is reserved via `self.staking_pool_account_ids.insert(&staking_pool_account_id)`, on success the callback whitelists the resulting pool automatically: [2](#0-1) 

Because the attacker fully controls `owner_id` and `reward_fee_fraction` for that specific, permanently-reserved account ID (`self.staking_pool_account_ids` never releases a successfully-created ID), any party who later expects to delegate to "the" staking pool at that name (e.g. via a lockup contract's `staking_pool_account_id` argument, which trusts any whitelisted pool) is instead interacting with a pool owned by the attacker. The custody binding broken is: *the account ID trusted by delegators as belonging to a specific operator* versus *the arguments (`owner_id`, `reward_fee_fraction`) actually used to deploy that account*. This is directly analogous to the external report's binding break: *the token registered as claimed by `createRJLaunchEvent`* versus *the token's rightful issuer*.

### Impact Explanation
If a delegator (directly, or indirectly through a lockup contract that is pointed at this pool name) stakes NEAR with the squatted pool, the attacker — as `owner_id` — can set/keep a `reward_fee_fraction` that captures a disproportionate share of staking rewards, i.e. rewards/fees are mis-attributed to the attacker rather than to the intended operator/delegators. This matches the "High" impact category (rewards or fees mis-attributed) in the rubric. Note: I was unable to fully confirm the exact upper bound enforced by `RewardFeeFraction::assert_valid()` (defined in `staking-pool-factory/src/utils.rs`) within the available tool budget, so I cannot state the maximum fee percentage an attacker could set; this should be verified before relying on a specific numeric ceiling.

### Likelihood Explanation
Likelihood is limited by cost and predictability: the attacker must know or guess the desired `staking_pool_id` in advance and pay the 30 NEAR minimum deposit (refunded only on outright creation failure, not recoverable once the ID is claimed successfully). This is costlier and requires more foreknowledge than the original 1-wei token-front-run, so likelihood is lower, but the mechanism (permissionless identifier-claiming with attacker-chosen trust parameters) is structurally the same.

### Recommendation
Restrict `create_staking_pool` (or at least the choice of `staking_pool_id`) so that it cannot be squatted by unrelated parties — e.g., require the pool ID to be namespaced under, or signed/authorized by, the intended pool operator, or require factory-owner/foundation approval before a newly created pool is auto-whitelisted, rather than whitelisting unconditionally on successful deployment.

### Proof of Concept
1. Attacker observes (or predicts) that a known operator, e.g. "MyStakingCo", intends to later deploy a pool with `staking_pool_id = "mystakingco"` on the shared `StakingPoolFactory`.
2. Attacker calls `create_staking_pool(staking_pool_id="mystakingco", owner_id=<attacker>, stake_public_key=<attacker key>, reward_fee_fraction={numerator: N, denominator: D})` with `MIN_ATTACHED_BALANCE` attached — see `staking-pool-factory/src/lib.rs:137-171`.
3. `self.staking_pool_account_ids.insert(...)` succeeds because the ID has never been used; account `mystakingco.<factory>` is created and deployed with the attacker as owner, then auto-whitelisted in `on_staking_pool_create` — `staking-pool-factory/src/lib.rs:197-224`.
4. The real "MyStakingCo" can never deploy a pool at that account ID again (the ID is permanently reserved in `staking_pool_account_ids`), and any user or lockup contract that later delegates to `mystakingco.<factory>` believing it belongs to the legitimate operator is actually delegating to the attacker-owned, attacker-fee-configured pool.

### Citations

**File:** staking-pool-factory/src/lib.rs (L137-171)
```rust
    pub fn create_staking_pool(
        &mut self,
        staking_pool_id: String,
        owner_id: AccountId,
        stake_public_key: Base58PublicKey,
        reward_fee_fraction: RewardFeeFraction,
    ) -> Promise {
        assert!(
            env::attached_deposit() >= MIN_ATTACHED_BALANCE,
            "Not enough attached deposit to complete staking pool creation"
        );

        assert!(
            staking_pool_id.find('.').is_none(),
            "The staking pool ID can't contain `.`"
        );

        let staking_pool_account_id = format!("{}.{}", staking_pool_id, env::current_account_id());
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The staking pool account ID is invalid"
        );

        assert!(
            env::is_valid_account_id(owner_id.as_bytes()),
            "The owner account ID is invalid"
        );
        reward_fee_fraction.assert_valid();

        assert!(
            self.staking_pool_account_ids
                .insert(&staking_pool_account_id),
            "The staking pool account ID already exists"
        );

```

**File:** staking-pool-factory/src/lib.rs (L197-224)
```rust
    /// Callback after a staking pool was created.
    /// Returns the promise to whitelist the staking pool contract if the pool creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_staking_pool_create(
        &mut self,
        staking_pool_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> PromiseOrValue<bool> {
        assert_self();

        let staking_pool_created = is_promise_success();

        if staking_pool_created {
            env::log(
                format!(
                    "The staking pool @{} was successfully created. Whitelisting...",
                    staking_pool_account_id
                )
                .as_bytes(),
            );
            ext_whitelist::add_staking_pool(
                staking_pool_account_id,
                &self.staking_pool_whitelist_account_id,
                NO_DEPOSIT,
                gas::WHITELIST_STAKING_POOL,
            )
            .into()
```
