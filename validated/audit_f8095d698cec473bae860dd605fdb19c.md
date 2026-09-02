Confirmed: `MultiSigContract::new` in `multisig/src/lib.rs` has no lower-bound check on `num_confirmations`, and `multisig2/src/lib.rs::new` only asserts `members.len() >= num_confirmations`, which is trivially satisfied when `num_confirmations == 0`. In `confirm`, the check `confirmations.len() as u32 + 1 >= self.num_confirmations` means with `num_confirmations == 0` the very first `add_request`/`add_request_and_confirm` call executes the request immediately, with effectively zero required confirmations — i.e., the K-of-N threshold binding (`confirmations_count >= num_confirmations`) is broken at K=0, allowing a single access key to unilaterally transfer funds, add/delete keys, or deploy code without any multisig approval. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Multisig contracts allow `num_confirmations = 0`, letting a single key execute requests without any confirmation threshold - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` initialize the K-of-N multisig threshold via a `new()` constructor that does not require `num_confirmations > 0`. Combined with the `confirm()` logic that executes a request once `confirmations.len() + 1 >= num_confirmations`, a contract initialized (or later reconfigured via `SetNumConfirmations`) with `num_confirmations = 0` will execute any request — including `Transfer`, `AddKey`, `DeleteKey`, `DeployContract`, `FunctionCall` — as soon as it is added, with zero confirming signatures.

### Finding Description
`multisig/src/lib.rs::new` simply stores the caller-supplied `num_confirmations: u32` with no validation: [1](#0-0) 

`multisig2/src/lib.rs::new` only checks `members.len() >= num_confirmations`, which holds trivially for `num_confirmations = 0` regardless of `members.len()`: [3](#0-2) 

The execution gate in `confirm()` is:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    // execute request
}
``` [2](#0-1) 

When `num_confirmations == 0`, this condition (`0 + 1 >= 0`) is always true, so `add_request_and_confirm` (which calls `add_request` then `confirm` in the same transaction) executes the request from a single key's single call — no additional confirmations are ever collected. The same `SetNumConfirmations` request action can also be used, once, to drop the threshold to `0` for all future requests, since `execute_request` assigns `self.num_confirmations = num_confirmations` with no validation either.

This breaks the intended K-of-N custody binding: the number of live confirming members required to move funds or change keys (`K`) versus the number actually collected before execution (should be `K`, becomes `0`).

### Impact Explanation
This matches the "Critical" class described in the rules: a multisig request executed below threshold. With `num_confirmations = 0`, any single holder of one of the multisig's function-call access keys (or any one designated member in multisig2) can unilaterally transfer all NEAR held by the contract, delete/add keys to seize full control, or deploy arbitrary contract code — completely bypassing the K-of-N protection the contract exists to provide.

### Likelihood Explanation
Exploitation requires the multisig to be deployed/initialized with `num_confirmations = 0`, or for the threshold to later be dropped to `0` via a single `SetNumConfirmations` request (itself executable by one key if the multisig started with `num_confirmations = 1` and that request type is "one action only"). While a `0`-confirmation multisig would typically be a misconfiguration rather than the common case, nothing in the contract code prevents it, and the `multisig-factory`/`create` flow at `multisig-factory/src/lib.rs` passes the caller-provided `num_confirmations` straight through without any validation, so a deploy script or UI bug can silently create a multisig with no real threshold. [4](#0-3) 

### Recommendation
Add an explicit assertion in both `multisig/src/lib.rs::new` and `multisig2/src/lib.rs::new` that `num_confirmations > 0` (and reasonably, `num_confirmations <= members.len()` for multisig2, which is already checked). Apply the same `num_confirmations > 0` check inside `execute_request`'s `SetNumConfirmations` handler so the threshold can never be dropped to zero after initialization either.

### Proof of Concept
1. Deploy `multisig` (or `multisig2`) with `new({"num_confirmations": 0, ...})` — no assertion rejects this.
2. A single holder of one access key (or one member in multisig2) calls `add_request_and_confirm` with a `Transfer` action moving the full contract balance to an attacker-controlled account.
3. Inside `confirm()`, `confirmations.len() as u32 + 1 (=1) >= self.num_confirmations (=0)` is true, so `execute_request` runs immediately, transferring the funds with zero required confirmations.

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

**File:** multisig/src/lib.rs (L246-260)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
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

**File:** multisig-factory/src/lib.rs (L29-49)
```rust
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```
