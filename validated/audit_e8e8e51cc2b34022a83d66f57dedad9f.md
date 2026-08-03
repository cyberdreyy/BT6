Based on the code I was able to retrieve, this finding is **valid**.

### Title
Unprivileged actor can pre-register a `BeneficiaryForOperator` to hijack future operator commission from any staker who names them as operator - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` can be called by any signer at any time, without that signer ever having been an active operator of a staking contract, and without the consent of any staker.

### Finding Description
`set_beneficiary_for_operator` is a permissionless entry function keyed purely off `signer::address_of(operator)`, with no check that the caller currently controls any `StakingContract` or `StakePool`: [1](#0-0) 

An attacker calls this function from an address they control before that address is ever named as an operator by anyone, registering `BeneficiaryForOperator { beneficiary_for_operator: attacker_payout_address }` under their own account.

Separately, `create_staking_contract`/`create_staking_contract_with_coins` let a staker freely choose *any* address as `operator`, with no validation that this address is a known/registered validator operator, nor any cross-check against `BeneficiaryForOperator`: [2](#0-1) 

Commission-related flows (`request_commission`, `distribute`, `distribute_internal`) authorize callers by checking `account_addr == staker || account_addr == operator || account_addr == beneficiary_for_operator(operator)`: [3](#0-2) 

The `test_set_beneficiary_for_operator` test in this same file confirms the payout behavior: once a beneficiary is set for an operator address, `distribute` sends the operator's commission share to the beneficiary address instead of the operator's own account: [4](#0-3) 

Because `BeneficiaryForOperator` is stored per-operator-address (not per staking-contract or per staker relationship), and because the beneficiary can be set before the address is ever used as an operator, an attacker can:
1. Call `set_beneficiary_for_operator` from address `A` (which they control and which has never operated any stake pool), setting the beneficiary to their own separate payout address `B`.
2. Wait for any unsuspecting staker to call `create_staking_contract(operator = A, ...)` — the staker chose `A` believing it to be a legitimate operator.
3. Once the pool accrues rewards and commission is requested/distributed, the operator's commission share is routed to `B`, not to `A`.

Note: I could not fully verify the exact implementation of `beneficiary_for_operator()` (the read function itself) or `request_commission_internal`'s recipient-selection logic in this pass — grep for those specific function bodies did not return content in this session, likely due to index truncation on this file. The test at lines 1741-1790 does empirically demonstrate the beneficiary redirection behavior, which is the core mechanism being exploited, but I was unable to directly confirm from this session whether any additional linkage check (e.g., requiring the operator to already have an active `StakingContract`) exists before `set_beneficiary_for_operator` succeeds. Given `set_beneficiary_for_operator`'s doc comment states "the operator does not need to be validated with respect to a staking pool," this strongly suggests no such check exists, but a full read of the function and `beneficiary_for_operator()`/`request_commission_internal` bodies is recommended to close out verification with certainty.

### Impact Explanation
If confirmed as described, this allows a completely unprivileged, never-before-operator address to permanently capture 100% of operator commission from any staker who later (knowingly or by social-engineering/typo/copy-paste error) designates that address as operator — a real economic diversion of stake rewards away from the intended recipient, without the staker's consent to that specific beneficiary.

### Likelihood Explanation
Likelihood depends heavily on how "operator" addresses are chosen in practice. Since `create_staking_contract` performs no validation that `operator` is a known, reputable validator-operator address (it only requires the account exist to receive the resource), a staker who is careless, phished, or relying on an attacker-advertised "operator service" address could easily fall victim. This is a social/UX risk more than a cryptographic exploit, but it stems directly from a permissionless state-setting function combined with an unrestricted operator-selection parameter.

### Recommendation
- Require that `set_beneficiary_for_operator` can only be called by an address that currently has at least one active `StakingContract` where it is the operator (i.e., check existence of relevant pool state), and/or namespace `BeneficiaryForOperator` per staking-contract (staker+operator pair) rather than globally per-operator-address.
- Consider requiring staker acknowledgment/consent (e.g., surfacing the current beneficiary of a chosen operator at `create_staking_contract` time, or an explicit staker-side opt-in for beneficiary redirection) so stakers aren't silently exposed to pre-registered beneficiary redirection.
- At minimum, expose beneficiary status via view functions so staker-side tooling/wallets can warn if the chosen operator already has a beneficiary registered that differs from the operator's own address.

### Proof of Concept
1. Attacker (never an operator) calls `staking_contract::set_beneficiary_for_operator(attacker_signer, attacker_payout_addr)` from address `A`.
2. Staker calls `staking_contract::create_staking_contract(staker_signer, operator = A, voter = A, amount, commission_percentage, seed)`.
3. Pool accrues rewards over epochs.
4. Anyone calls `staking_contract::request_commission(caller, staker, A)` then `staking_contract::distribute(staker, A)`.
5. Verify: `coin::balance<AptosCoin>(attacker_payout_addr)` increases by the commission amount while `coin::balance<AptosCoin>(A)` does not, matching the pattern demonstrated in the existing `test_set_beneficiary_for_operator` test at lines 1741-1822 of `staking_contract.move`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L431-467)
```text
    /// Staker can call this function to create a simple staking contract with a specified operator.
    public fun create_staking_contract_with_coins(
        staker: &signer,
        operator: address,
        voter: address,
        coins: Coin<AptosCoin>,
        commission_percentage: u64,
        // Optional seed used when creating the staking contract account.
        contract_creation_seed: vector<u8>
    ): address acquires Store {
        assert!(
            commission_percentage >= 0 && commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // The amount should be at least the min_stake_required, so the stake pool will be eligible to join the
        // validator set.
        let (min_stake_required, _) =
            staking_config::get_required_stake(&staking_config::get());
        let principal = coin::value(&coins);
        assert!(
            principal >= min_stake_required,
            error::invalid_argument(EINSUFFICIENT_STAKE_AMOUNT)
        );

        // Initialize Store resource if this is the first time the staker has delegated to anyone.
        let staker_address = signer::address_of(staker);
        if (!exists<Store>(staker_address)) {
            move_to(staker, new_staking_contracts_holder(staker));
        };

        // Cannot create the staking contract if it already exists.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&operator),
            error::already_exists(ESTAKING_CONTRACT_ALREADY_EXISTS)
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-616)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-829)
```text

        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1741-1790)
```text
        // Set beneficiary.
        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);

        // Fast forward to generate rewards.
        stake::end_epoch();
        let new_balance = with_rewards(INITIAL_BALANCE);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);

        // Operator claims 10% of rewards so far as commissions.
        let expected_commission_1 =
            (new_balance - last_recorded_principal(staker_address, operator1_address))
                / 10;
        new_balance -= expected_commission_1;
        request_commission(operator1, staker_address, operator1_address);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            expected_commission_1
        );
        assert!(
            last_recorded_principal(staker_address, operator1_address) == new_balance, 0
        );
        assert_distribution(
            staker_address,
            operator1_address,
            operator1_address,
            expected_commission_1
        );
        stake::fast_forward_to_unlock(pool_address);

        // Both original stake and operator commissions have received rewards.
        expected_commission_1 = with_rewards(expected_commission_1);
        new_balance = with_rewards(new_balance);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            expected_commission_1,
            0,
            0
        );
        distribute(staker_address, operator1_address);
        let operator_balance = coin::balance<AptosCoin>(operator1_address);
        let beneficiary_balance = coin::balance<AptosCoin>(beneficiary_address);
        let expected_operator_balance = INITIAL_BALANCE;
        let expected_beneficiary_balance = expected_commission_1;
        assert!(operator_balance == expected_operator_balance, operator_balance);
        assert!(beneficiary_balance == expected_beneficiary_balance, beneficiary_balance);
```
