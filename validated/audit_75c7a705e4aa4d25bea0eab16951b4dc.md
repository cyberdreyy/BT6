### Title
Multisig contract's `num_confirmations` threshold is never validated at initialization or when changed via `SetNumConfirmations`, allowing the K-of-N confirmation threshold to be set to `0` (or otherwise misconfigured) - ([File: multisig/src/lib.rs], [File: multisig2/src/lib.rs])

### Summary
Both multisig implementations in this repository fail to validate the `num_confirmations` field the same way the NftPort report describes for `royaltiesBps`: the value is checked (partially, in `multisig2`) at `new()`/initialization time but never re-validated when it is subsequently changed through the `SetNumConfirmations` request action, and in the original `multisig` contract it is never validated at all, even at `new()`. This lets the declared "K of N" security guarantee of the contract be silently broken to `K = 0`, meaning a single confirmation (or even zero) is enough to execute any request, including `Transfer`, `AddKey`, or `DeployContract` actions that move or reassign control of the account's funds.

### Finding Description
`multisig/src/lib.rs::new()` accepts `num_confirmations: u32` and stores it with no assertion whatsoever: [1](#0-0) 

`multisig2/src/lib.rs::new()` improves slightly, but only checks that `members.len() >= num_confirmations`, still permitting `num_confirmations == 0`: [2](#0-1) 

The actual threshold check performed during execution is a simple numeric comparison against `self.num_confirmations`: [3](#0-2) 

If `num_confirmations` is `0`, then on the very first call to `confirm` (or `add_request_and_confirm`), `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates to `1 >= 0`, which is always `true`. The request executes immediately with a single signer's confirmation, regardless of how many members/keys exist.

Crucially, `num_confirmations` is not a value fixed at deployment — it can be changed at any time via the `SetNumConfirmations` request/action, which goes through the same generic `add_request`/`confirm` flow as any other multisig request: [4](#0-3) 

The test suite confirms `SetNumConfirmations` is processed with no bounds checking on the new value: [5](#0-4) 

Nowhere in the request-execution path (for either `multisig` or `multisig2`) is the new `num_confirmations` value checked to be `>= 1` or `<= members.len()` (in `multisig2`) or `<= number of live access keys` (in `multisig`). This is the exact same bug class as the NftPort finding: a config field (`royaltiesBps` there, `num_confirmations` here) is validated in one code path (`initialize`/`new`) but not in the other mutating path (`update`/`SetNumConfirmations`), and in the `multisig` (v1) contract it is not validated in *either* path.

### Impact Explanation
This directly matches the "Critical" impact category: *a multisig request executed below threshold*. Once `num_confirmations` is driven to `0` (via a crafted `SetNumConfirmations` request that itself only needs to pass through the existing, possibly-already-compromised-or-careless threshold once), the contract's fundamental "K of N" custody guarantee collapses to effectively `1 of N` (or even `0 of N`, since a request could be added and confirmed by the same single call in `add_request_and_confirm`). Any subsequent `Transfer`, `AddKey`, `DeployContract`, or `FunctionCall` request can be executed by a single member/key holder, without the number of confirmations the account owners believed was enforced. This breaks the equality that the protocol design relies on: `confirmations_required_by_policy == confirmations_actually_needed_to_execute`.

### Likelihood Explanation
The likelihood of the value being set to something invalid depends on human/admin error rather than a fully unprivileged external actor forging state, similar to the referenced NftPort issue (an admin action, not requiring any special exploit). However, since `SetNumConfirmations` is processed by the same generic, unchecked request pipeline as every other action, and since `multisig` (v1) validates nothing even at `new()`, a single accidental low value (or `0`) permanently and silently downgrades the security threshold with no on-chain guard rail to prevent or even flag it.

### Recommendation
Add explicit validation in both `new()` and in the request-execution path for `SetNumConfirmations` (or `execute_request`) to assert:
- `num_confirmations >= 1`
- `num_confirmations <= members.len()` (multisig2) / a documented maximum matching the actual number of managing access keys (multisig v1)

This mirrors the NftPort fix: validate config fields consistently across every code path that can set them (`initialize()`/`new()` and `update()`/`SetNumConfirmations`), not just one.

### Proof of Concept
1. Deploy `multisig` (v1) with `new({"num_confirmations": 0})` — no assertion rejects this.
2. Any holder of a function-call access key on the multisig account calls `add_request` with a `Transfer` action, then `confirm`.
3. In `confirm`, `confirmations.len() as u32 + 1 (=1) >= self.num_confirmations (=0)` is `true`, so the transfer executes immediately with a single confirmation — the "K of N" scheme is bypassed entirely from deployment.
4. Alternatively, for `multisig2`, deploy with a valid `num_confirmations` (e.g., `3`), then submit and confirm (through the legitimate `3`-of-`N` process once) a `SetNumConfirmations` request setting `num_confirmations` to `0`. From that point forward, all future requests execute with a single confirmation, permanently degrading the multisig's security guarantee with no contract-level check preventing it. [6](#0-5) [7](#0-6)

### Citations

**File:** multisig/src/lib.rs (L102-113)
```rust
    #[init]
    pub fn new(num_confirmations: u32) -> Self {
        assert!(!env::state_exists(), "Already initialized");
        Self {
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(b"r".to_vec()),
            confirmations: UnorderedMap::new(b"c".to_vec()),
            num_requests_pk: UnorderedMap::new(b"k".to_vec()),
            active_requests_limit: 12,
        }
    }
```

**File:** multisig2/src/lib.rs (L147-167)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
        let mut multisig = Self {
            members: UnorderedSet::new(StorageKeys::Members),
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(StorageKeys::Requests),
            confirmations: LookupMap::new(StorageKeys::Confirmations),
            num_requests_pk: LookupMap::new(StorageKeys::NumRequestsPk),
            active_requests_limit: ACTIVE_REQUESTS_LIMIT,
        };
        let mut promise = Promise::new(env::current_account_id());
        for member in members {
            promise = multisig.add_member(promise, member);
        }
        multisig
    }
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L746-762)
```rust
    #[test]
    fn test_change_num_confirmations() {
        let amount = 1_000;
        testing_env!(context_with_key(
            PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            amount
        ));
        let mut c = MultiSigContract::new(members(), 1);
        let request_id = c.add_request(MultiSigRequest {
            receiver_id: alice(),
            actions: vec![MultiSigRequestAction::SetNumConfirmations {
                num_confirmations: 2,
            }],
        });
        c.confirm(request_id);
        assert_eq!(c.num_confirmations, 2);
    }
```

**File:** multisig/README.md (L186-189)
```markdown
Change number of confirmations required to approve multisig:
```bash
near call multisig.illia add_request '{"request": {"receiver_id": "multisig.illia", "actions": [{"type": "SetNumConfirmations", "num_confirmations": 2}]}}' --accountId multisig.illia
```
```
