### Title
Multisig contracts accept an unvalidated `num_confirmations` threshold, allowing a zero-confirmation multisig - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The `new()` initializer of both multisig contract versions never validates that `num_confirmations` is greater than zero (or bounded to a sane minimum). A deployer can initialize either contract with `num_confirmations = 0`, effectively creating a multisig that requires zero signatures to execute any request, which breaks the "k of n" invariant advertised by the contract.

### Finding Description
`multisig/src/lib.rs`'s `new()` only checks that state doesn't already exist and stores whatever `num_confirmations` value is passed, with no lower-bound check at all: [1](#0-0) 

`multisig2/src/lib.rs`'s `new()` is only slightly stricter — it asserts `members.len() >= num_confirmations`, which is trivially satisfied when `num_confirmations = 0` regardless of member count: [2](#0-1) 

`multisig-factory/src/lib.rs`'s `create()` forwards an attacker-supplied `num_confirmations: u64` straight through to the deployed multisig2 contract's `new` call without any validation on the factory side either: [3](#0-2) 

The binding that should hold is: `confirmations_required(request) >= 1` for every executable request, i.e. `num_confirmations >= 1`. With `num_confirmations = 0`, the threshold check in `confirm()`/`execute_request()` (`confirmations.len() >= self.num_confirmations`) is satisfied immediately, so any single member (or even a non-member call path depending on `current_member()` resolution) can push a request straight to execution without ever collecting a second signature — turning a "k of n" multisig into effectively no multisig at all. Because `num_confirmations` can later only be changed via `SetNumConfirmations`, which itself is gated by the same broken threshold, a `num_confirmations = 0` deployment is a self-perpetuating single-signer contract masquerading as an n-of-n or k-of-n custody boundary.

This is the direct structural analog of the reported `royaltyFraction`/immutable-parameter issue: an unchecked numeric parameter set once at construction time silently degrades a security guarantee (royalty economics vs. multisig confirmation threshold) with no bound enforced by the constructor.

### Impact Explanation
This falls squarely under the "Critical" impact bucket: "a multisig request executed below threshold." Any request (including `Transfer`, `FunctionCall`, `AddKey`/full-access-key grants) added to a `num_confirmations = 0` multisig can be executed with confirmations strictly below the intended threshold, allowing funds custodied by the multisig account to move, or full-access keys to be added, without the quorum of members the deployer/users believed was enforced.

### Likelihood Explanation
This requires only a deployment-time mistake (or a malicious deployer creating a facade "secure" multisig) rather than any privileged runtime action. Since `multisig-factory` and both `multisig`/`multisig2` `new()` entrypoints are public `#[init]` functions callable by anyone deploying the contract, and neither validates `num_confirmations >= 1`, the misconfiguration is trivially reachable and, once deployed, is effectively permanent because raising the threshold itself needs to pass through the broken (zero-threshold) confirmation gate.

### Recommendation
Add an explicit lower-bound check in both `new()` implementations, e.g.:
```rust
assert!(num_confirmations >= 1, "num_confirmations must be at least 1");
```
in `multisig/src/lib.rs::new` and `multisig2/src/lib.rs::new` (in addition to the existing `members.len() >= num_confirmations` check), and mirror the same validation in `multisig-factory/src/lib.rs::create` before forwarding the parameter, so a zero-threshold contract can never be deployed.

### Proof of Concept
1. Attacker calls `multisig-factory::create` (or deploys `multisig`/`multisig2` directly) with `num_confirmations = 0` and any member list. [3](#0-2) 
2. The deployed contract's `new()` accepts this value unmodified: [4](#0-3) 
3. Attacker (as a member, or the sole key holder) calls `add_request_and_confirm` with a `Transfer` action targeting their own account: [5](#0-4) 
4. Because `self.confirmations.len() >= self.num_confirmations` (`>= 0`) is satisfied on the very first (and only) confirmation, `execute_request` runs and the transfer executes despite the confirmation count being below any meaningful quorum — funds move out of the multisig-controlled account without the "k of n" guarantee the contract advertises.

### Citations

**File:** multisig/src/lib.rs (L100-113)
```rust
    /// Initialize multisig contract.
    /// @params num_confirmations: k of n signatures required to perform operations.
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

**File:** multisig2/src/lib.rs (L202-207)
```rust
    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
    }
```

**File:** multisig-factory/src/lib.rs (L28-49)
```rust
    #[payable]
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
