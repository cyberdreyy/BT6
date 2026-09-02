### Title
Multisig confirmations are not invalidated on membership change, allowing a request to execute with fewer live-member confirmations than `num_confirmations` requires - (File: multisig2/src/lib.rs)

### Summary
The `MultiSigContract::confirm()` function in `multisig2/src/lib.rs` counts confirmations that were recorded by member identifiers (`String` representations of `MultisigMember`) stored in `self.confirmations: LookupMap<RequestId, HashSet<String>>` [1](#0-0) . These confirmation records are never pruned or revalidated when the underlying member is later removed via `DeleteMember`. As a result, a stale confirmation from a since-removed member can still be counted toward `self.num_confirmations` when a request is later confirmed, letting the request execute even though the number of *live* confirming members is below the configured threshold.

### Finding Description
`confirm()` only checks that the confirming key/account hasn't already confirmed, and then compares the *size* of the stored confirmations set to `self.num_confirmations`: [2](#0-1) 

```rust
pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
    self.assert_valid_request(request_id);
    let member = self.current_member()...;
    let mut confirmations = self.confirmations.get(&request_id).unwrap();
    assert(!confirmations.contains(&member.to_string()), "Already confirmed...");
    if confirmations.len() as u32 + 1 >= self.num_confirmations {
        let request = self.remove_request(request_id);
        self.execute_request(request)
    } else {
        confirmations.insert(member.to_string());
        self.confirmations.insert(&request_id, &confirmations);
        PromiseOrValue::Value(true)
    }
}
```

There is no check that every `String` already present in `confirmations` corresponds to a member that is still in `self.members` at execution time. The `AddMember`/`DeleteMember` actions in `execute_request` mutate `self.members` directly but do not touch `self.confirmations` for any in-flight requests: [3](#0-2) 

Because `self.confirmations` is only keyed by `request_id` and never cross-referenced against `self.members` at the moment the threshold is evaluated, the equality the contract is supposed to guarantee — *live confirming members ≥ num_confirmations* — can be broken to *stored confirmation strings ≥ num_confirmations* even though one or more of those strings belong to accounts/keys no longer in the member set.

Concretely: with `num_confirmations = 2` and members `{A, B, C}`:
1. `A` calls `add_request_and_confirm` on transfer request `R` → `confirmations[R] = {A}` (1/2).
2. Members execute a separate `DeleteMember { member: A }` request (via its own independent k-of-n approval), removing `A` from `self.members`.
3. `C` (a current, single live member) calls `confirm(R)`. `confirmations.len() + 1 == 2 >= num_confirmations`, so `R` executes the `Transfer`.

The transfer was authorized by only **one currently-live member (`C`)** plus a stale confirmation from a **removed member (`A`)**, not by two live members as the k-of-n scheme is designed to require.

### Impact Explanation
This breaks the core custody guarantee of the multisig: a `Transfer`, `FunctionCall`, `AddKey`, or any other `MultiSigRequestAction` can be executed with fewer live authorized confirmations than `num_confirmations` mandates. This directly matches the Critical impact category "a multisig request executed below threshold" — NEAR (or any action gated by the multisig, including key/member changes) can move or be authorized without the intended quorum of currently-trusted parties.

### Likelihood Explanation
This requires no special privilege beyond being one of the remaining legitimate members at the time of the final `confirm()` call (or the original confirmer no longer needing to still be a member). Because membership changes (onboarding/offboarding) are an expected, routine multisig operation, and pending requests naturally persist across such changes (no explicit invalidation), the sequence of events (confirm → remove a different member → confirm again) is a realistic operational pattern, not a contrived edge case.

### Recommendation
When evaluating whether a request has reached quorum, filter `confirmations` to only those entries whose corresponding `MultisigMember` is still present in `self.members`, or proactively prune/invalidate `self.confirmations` entries for a member as part of `DeleteMember` execution. Additionally, `SetNumConfirmations` should re-assert `self.members.len() >= num_confirmations` (mirroring the check in `new()`) to avoid an equivalent freezing issue when confirmations are legitimately required from live members only.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. `A` calls `add_request_and_confirm(Transfer{...})` → request `R` has `confirmations = {A}`.
3. Separately, members reach quorum on `DeleteMember{member: A}` and it executes, removing `A` from `self.members`. (`self.confirmations[R]` is untouched — see `execute_request`, no reference to the `confirmations` map for `AddMember`/`DeleteMember`.)
4. `C` calls `confirm(R)`. `confirmations.len() (1) + 1 == 2 >= num_confirmations (2)` → `execute_request` runs the `Transfer`, moving funds with only `C` as a live confirming member instead of the required 2.

### Citations

**File:** multisig2/src/lib.rs (L108-133)
```rust
#[derive(BorshStorageKey, BorshSerialize)]
pub enum StorageKeys {
    Members,
    Requests,
    Confirmations,
    NumRequestsPk,
}

#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
```

**File:** multisig2/src/lib.rs (L235-242)
```rust
                MultiSigRequestAction::AddMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.add_member(promise, member)
                }
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
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
