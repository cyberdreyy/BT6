### Title
Stale confirmations from removed multisig members still count toward the execution threshold, allowing a request to execute below the required number of live confirmations - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` tallies a request's approval purely by the size of the `confirmations` `HashSet<String>` recorded for that `request_id`. Membership is validated only at the moment each individual confirmation is cast (via `current_member()` inside `assert_valid_request`), never re-validated when the tally is checked against `num_confirmations` or when the request is finally executed. When a member is removed via `DeleteMember`, `delete_member` only purges confirmations for requests that member *originally created* (`r.member == member`), not confirmations that member *cast on other members' requests*. A removed member's stale confirmation therefore continues to count, letting a request execute with fewer real, live approvals than `num_confirmations` requires. [1](#0-0) 

### Finding Description
The binding that should hold is:
```
confirmations counted at execution time == confirmations from accounts that are still members at execution time
```

Instead, `confirm()` reads the raw stored set:
```rust
let mut confirmations = self.confirmations.get(&request_id).unwrap();
...
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [2](#0-1) 

`current_member()`/`assert_valid_request` only checks whether the *caller confirming right now* is a live member — it does not re-check whether previously recorded confirmations in the set are still held by live members. [3](#0-2) 

The only place stale confirmations are cleaned up is `delete_member`, but it filters by `r.member == member`, i.e., it only clears confirmations tied to requests *that member authored*, not confirmations that member cast on requests authored by someone else:
```rust
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [4](#0-3) 

So a confirmation cast by a member who is later removed from the multisig remains in the `confirmations` set for any request created by a *different* member, and it keeps counting toward `num_confirmations`. The equivalent legacy `multisig/src/lib.rs` has the same pattern: `DeleteKey` only removes requests where `r.signer_pk == pk` (the requester's key), leaving stale confirmations from that key on other requests intact. [5](#0-4) [6](#0-5) 

This is the same class of bug as the reported issue: a validity check (`allowedSender`/membership) is scoped only to a single, initial event (order creation / confirmation cast) instead of being enforced continuously against the current authoritative state (order lifetime / live member set) whenever the security-critical decision (fill / execution) is made.

### Impact Explanation
This crosses the authorization/threshold boundary called out as Critical impact: "a multisig request executed below threshold." An attacker (or a legitimate member who is later removed, maliciously or in collusion) can get a `Transfer`, `AddKey`, `FunctionCall`, or other privileged `MultiSigRequestAction` executed with fewer genuinely live confirmations than `num_confirmations` mandates, because a removed member's earlier confirmation is never purged from requests they did not author. This can result in funds moved, keys added, or contract state changed by a multisig that no longer has the security level ("k of n") it was configured and audited for.

### Likelihood Explanation
This requires the ordinary governance flow of the multisig itself: some member confirms a pending request, and later that member (or another member) is removed via a normal `DeleteMember`/`DeleteKey` action — both are core, expected multisig operations, not privileged-attacker-only or foundation-only actions. No redeploy, no owner privilege beyond being one of the multisig's own members, and no assumption that initialization is skipped is required; it purely depends on ordinary operational sequencing (confirm, then remove a different member, then finish confirming). This makes it readily reachable in normal contract usage.

### Recommendation
When tallying confirmations in `confirm()` (and before `execute_request`), filter the stored confirmation set to only the entries corresponding to accounts/keys currently present in `self.members` (or `self.num_requests_pk`/access-key membership for the legacy contract), rather than trusting the raw `HashSet` size. Alternatively, when a member is deleted, iterate all requests' confirmation sets (not just requests they authored) and drop any confirmation entry belonging to the removed member, keeping `confirmations` and `members` mutually consistent at all times.

### Proof of Concept
1. Deploy `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A` calls `add_request(R)` (e.g., `Transfer` action) — creates R with empty confirmations.
3. `B` calls `confirm(R)` → `confirmations(R) = {B}`.
4. `C` calls `confirm(R)` → `confirmations(R) = {B, C}` (still below 3, no execution).
5. Separately, the multisig executes a fully-confirmed `DeleteMember { member: C }` request (this only removes requests *authored by* C — R was authored by A, so R and its confirmations, including C's, are untouched):
`self.confirmations.get(&R)` still `= {B, C}` even though `C` is no longer in `self.members`.
6. `D` calls `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations`, so `execute_request(R)` runs.
7. Result: R executes with `num_confirmations = 3` satisfied, but only `B` and `D` are actually live members who approved it — `C`'s stale confirmation (from before removal) was counted, so the request executed below the intended live-member threshold. [2](#0-1) [1](#0-0)

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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
