### Title
Stale confirmations from removed multisig members/keys are never purged from unrelated pending requests, allowing a request to execute below the live-member confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` and `multisig/src/lib.rs` counts confirmations stored per `request_id` against `num_confirmations` without verifying that every recorded confirmer is still a current member (or holds a valid access key). When a member/key is removed via `DeleteMember`/`DeleteKey`, only requests that member *originally created* are purged; confirmations that member cast on *other, still-pending* requests are left untouched. This lets a request execute using a mix of stale (revoked) approvals and current approvals that together satisfy the numeric threshold, while the number of actually-entitled (live) approvers is strictly less than `num_confirmations`.

### Finding Description
`confirm()` decides whether to execute a request purely by set size: [1](#0-0) 

The confirmation set is a bag of member identifiers (`HashSet<String>`), keyed independently of the current `members` set: [2](#0-1) 

When a member is removed, `delete_member` only scrubs requests that member itself authored (`r.member == member`); it does not scan `confirmations` on other requests to strip that member's vote: [3](#0-2) 

The equivalent v1 contract has the same gap: `DeleteKey` removes requests signed (created) by the deleted key, but not that key's confirmations recorded on requests created by other keys: [4](#0-3) [5](#0-4) 

Binding that should hold: `confirmations_counted(request) ⊆ current_members` at execution time, i.e. every string in `confirmations[request_id]` must correspond to an entry still present in `self.members` (or, for v1, a still-registered access key). The code instead accepts `confirmations_counted(request) ⊇ {any member/key ever confirmed, live or not}`, breaking that equality once membership changes after a confirmation is cast but before the request reaches quorum.

### Impact Explanation
This breaks the core custody guarantee of the multisig: `num_confirmations` is meant to represent independent live-signer approvals before funds move, keys are added, or contract code is deployed. With this flaw, a threshold of `k` can be met with fewer than `k` currently-entitled approvers — e.g. a `Transfer`, `AddKey`/`AddMember`, or `DeployContract` action can execute after a member who confirmed it is subsequently removed, using only `k-1` live approvals plus the stale one. This falls under "a multisig request executed below threshold," a Critical-impact category, since it can result in NEAR being moved, a full-access key added, or code deployed with less real consensus than configured.

### Likelihood Explanation
Any current member can trigger this deterministically: confirm a pending request, then (with the same or other members) pass a `DeleteMember`/`DeleteKey` request removing that confirmer, then let any remaining member supply the final confirmation. No privileged foundation role, redeploy, or race condition against validators is required — only ordinary multisig operations already exposed to members, making the path fully reachable by an authorized-but-limited actor seeking to under-collect real consensus.

### Recommendation
When counting confirmations in `confirm()`, filter the stored confirmation set to only those entries still present in `self.members` (v2) / still-registered signer keys (v1) before comparing against `num_confirmations`. Additionally, on `DeleteMember`/`DeleteKey`, iterate all pending requests' confirmation sets (not just requests authored by that member) and remove the departing member's/key's confirmation from each.

### Proof of Concept
Using `multisig2::MultiSigContract` with `members = [A, B, C, D]`, `num_confirmations = 3`:
1. `A` calls `add_request(Transfer{...})` → `request_id = R1`.
2. `B` calls `confirm(R1)` → `confirmations(R1) = {B}` (1 < 3, not executed).
3. `C` calls `confirm(R1)` → `confirmations(R1) = {B, C}` (2 < 3, not executed).
4. Members `A`, `C`, `D` create and confirm a separate `DeleteMember{member: B}` request (3/3, meets quorum for that action) → executed via: [6](#0-5) 
   `B` is removed from `self.members`, but `confirmations(R1)` still equals `{B, C}` because `delete_member` only scans requests where `r.member == B` (R1 was authored by `A`, so it is untouched).
5. `D` (still a live member) calls `confirm(R1)` → check `confirmations.len() + 1 = 3 >= num_confirmations (3)` passes, and the `Transfer` executes, using only 2 genuinely live approvers (`C`, `D`) plus the phantom, revoked approval of `B`.

### Citations

**File:** multisig2/src/lib.rs (L126-133)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
```

**File:** multisig2/src/lib.rs (L239-242)
```rust
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

**File:** multisig/src/lib.rs (L246-266)
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
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
