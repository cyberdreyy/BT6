## Title
Removing a multisig member does not purge their confirmations from *other* pending requests, allowing a request to execute with fewer live-member confirmations than `num_confirmations` — ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only removes requests that were *created* by the departing member, and clears confirmations only for *those* requests. It never scans other still-pending requests to strip the departing member's stale confirmation. Because `confirm()` counts confirmations purely by `HashSet` length (`confirmations.len() as u32 + 1 >= self.num_confirmations`), a stale confirmation from a removed member is still counted toward the threshold, letting a request execute with fewer confirmations from *currently valid* members than the configured `num_confirmations`.

### Finding Description
`confirm()` treats the recorded confirmation set as ground truth for how many members have approved a request: [1](#0-0) 

`delete_member` is the only place that prunes confirmations tied to a removed member, but it filters by `r.member == member` — i.e., only requests *proposed* by that member — not requests that the member *confirmed*: [2](#0-1) 

Consequently, if member `B` confirms a request `R` proposed by member `A`, and later `B` is removed via `delete_member` (a separate, properly-confirmed multisig action), `R`'s confirmation set in the `confirmations: LookupMap<RequestId, HashSet<String>>` map still contains `B`'s identity string. The only invariant enforced on removal is that the *remaining* member count is still `>= num_confirmations`: [3](#0-2) 

but this says nothing about whether `R`'s existing confirmations came from members still in the set. When a live member later calls `confirm(R)`, the length check adds the caller to `B`'s stale entry and can reach `num_confirmations` while only `num_confirmations - 1` (or fewer) *live* members ever actually approved it: [4](#0-3) 

This breaks the intended equality `confirmations counted == live members who approved`. The recorded claim (`confirmations.len()`) diverges from the true number of currently-authorized approvers, letting the multisig execute a transfer, contract deployment, key addition, or `FunctionCall` action below the configured threshold.

The original `multisig/src/lib.rs` (v1) has the analogous flaw: `DeleteKey` only clears confirmations for requests whose `signer_pk` equals the removed key, not confirmations that key contributed to other requests: [5](#0-4) 

### Impact Explanation
This is Critical per the listed impact classes: "a multisig request executed below threshold." Any pending, not-yet-executed request that received a confirmation from a member who is subsequently removed retains that confirmation permanently. The remaining members (fewer confirmations are then needed from currently-live signers) can push the request through `confirm()`, executing arbitrary `MultiSigRequestAction`s — including `Transfer`, `DeployContract`, or `AddKey`/`AddMember` — with less real authorization than the multisig's own security parameter `num_confirmations` guarantees.

### Likelihood Explanation
This requires no attacker privilege beyond being (or colluding with) a still-valid multisig member, and no special conditions beyond ordinary multisig operation: a member confirms a request, is later removed for any legitimate reason (compromise, offboarding, rotation), and the request is still pending. Since `delete_request` has a cooldown but no forced cleanup of confirmations either, stale confirmations can persist indefinitely until the request is separately deleted. This is a straightforward, deterministic outcome of the existing code path, not a theoretical edge case.

### Recommendation
When removing a member in `delete_member` (and `DeleteKey`/`DeleteMember` in the legacy `multisig/src/lib.rs`), iterate over **all** pending requests' confirmation sets (not just those proposed by the removed member) and strip the removed member's identity from each. Alternatively, validate at `confirm()`/execution time that every entry in the confirmation set still corresponds to a current member, e.g. by intersecting `confirmations` with `self.members` before comparing the count to `num_confirmations`.

### Proof of Concept
Given `num_confirmations = 2`, `members = {A, B, C}`:
1. `A` calls `add_request(R)` (no auto-confirm) → `confirmations[R] = {}`.
2. `B` calls `confirm(R)` → `1 + 1 = 2 >= 2`? No (len before insert is 0) → `confirmations[R] = {B}`.
3. A separate, properly-confirmed multisig request removes `B` via `MultiSigRequestAction::DeleteMember` → `delete_member` runs; since `R.member == A ≠ B`, `R` is untouched; `confirmations[R]` remains `{B}`. `members = {A, C}`.
4. `C` calls `confirm(R)`: `confirmations.len() (1, containing removed member B) + 1 = 2 >= num_confirmations (2)` → `execute_request(R)` runs.

Result: `R` executes with only one live-member confirmation (`C`), while `num_confirmations = 2` was supposed to require two currently-valid members to approve it. This can be verified by extending the existing test harness pattern in [6](#0-5)  with the above sequence and asserting `confirmations.len()` after removal still includes the departed member.

### Citations

**File:** multisig2/src/lib.rs (L142-167)
```rust
#[near_bindgen]
impl MultiSigContract {
    /// Initialize multisig contract.
    /// @params members: list of {"account_id": "name"} or {"public_key": "key"} members.
    /// @params num_confirmations: k of n signatures required to perform operations.
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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
