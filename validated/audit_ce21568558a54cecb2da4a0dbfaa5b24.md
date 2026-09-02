### Title
Stale confirmations from removed multisig members are still counted toward quorum, letting a request execute below the live confirmation threshold - (multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
Both `multisig` and `multisig2` contracts store confirmations for a request in a `HashSet` keyed only by the confirming key/member. When a member (or access key) is removed via `DeleteKey`/`DeleteMember`, the contract only purges requests *created* by that member and their own `num_requests_pk` counters — it never scans other requests' confirmation sets to strip a confirmation that member previously cast. A stale confirmation from a since-removed member therefore continues to count toward `num_confirmations` on any request it had confirmed before removal, allowing that request to later execute with fewer *live* member confirmations than the configured threshold.

### Finding Description
`confirm()` in both contracts computes eligibility for execution purely from the size of the stored confirmation set plus the new confirmer, without validating that every prior confirmer in that set is still a current member: [1](#0-0) 

Membership/key removal (`DeleteMember` / `DeleteKey`) only cleans up requests whose `member`/`signer_pk` equals the removed party — i.e. requests *they created* — and removes their `num_requests_pk` entry. It does not iterate over `confirmations` for requests created by *other* members to drop any confirmation the removed member had previously cast on those requests: [2](#0-1) 

The equivalent logic exists in the original `multisig` contract's `DeleteKey` handling: [3](#0-2) 

The binding broken is: `confirmations counted for a request` should equal `confirmations cast by currently-live members`. Instead, `confirmations.len()` can include confirmers who are no longer members, so a request can reach `num_confirmations` and execute even though the number of *live* confirming members is strictly less than `num_confirmations`.

### Impact Explanation
This lets a multisig request (e.g. a `Transfer`, `AddKey`/`AddMember`, or `FunctionCall`) execute with fewer live confirmations than the configured `num_confirmations` threshold, effectively bypassing the k-of-n authorization guarantee the contract is meant to enforce. This matches the "a multisig request executed below threshold" Critical impact category: funds can be moved, or privileged keys/members added, without the intended quorum of currently-authorized signers actually approving.

### Likelihood Explanation
This requires no special privilege beyond being (at some point) a legitimate multisig member/key — the same unprivileged-relative-to-quorum scenario contemplated by the rules ("confirmations counted versus live members"). It only requires: a member confirms a pending request, is later removed via a normal `DeleteMember`/`DeleteKey` request (a routine, expected operational action, e.g. offboarding a signer), and the original request is later pushed over threshold by remaining members who are unaware the stale confirmation is still silently counted.

### Recommendation
When removing a member/key (`DeleteMember`/`DeleteKey`), iterate over all active requests' `confirmations` sets (not just requests created by that member) and remove any entry belonging to the removed member/key. Alternatively, validate at `confirm()`-time (and at execution threshold-check time) that every entry in the stored confirmation set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3` (see `new()`: [4](#0-3) ).
2. Member `A` calls `add_request` to create request `R1` (e.g. `Transfer`).
3. Member `B` calls `confirm(R1)` → `confirmations = {B}` (1 < 3, stored via [1](#0-0) ).
4. Member `D` calls `confirm(R1)` → `confirmations = {B, D}` (2 < 3).
5. Members create and confirm a separate `DeleteMember { member: D }` request that reaches quorum and executes, removing `D` from `members` via `delete_member()` — this only removes requests *created by* `D` and `D`'s `num_requests_pk` entry; `R1`'s confirmation set `{B, D}` is untouched ( [2](#0-1) ).
6. Now only `A`, `B`, `C` are live members, but `num_confirmations` is still 3.
7. Member `C` calls `confirm(R1)`: `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `R1` executes ( [5](#0-4) ), even though only `B` and `C` — 2 live members — actually approved it, one confirmation short of the required 3-of-n live threshold.

### Citations

**File:** multisig2/src/lib.rs (L144-167)
```rust
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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
