### Title
Stale confirmations from deleted members remain counted toward `num_confirmations`, allowing execution below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`confirm()` tallies a request's approvals purely from the `confirmations: LookupMap<RequestId, HashSet<String>>` set, without re-checking that every string in that set still belongs to a member currently present in `self.members`. `delete_member()` only purges confirmations/requests that were *originated* by the removed member (`r.member == member`), not the confirmations that member *added* to other members' pending requests. A confirmation cast by a member that is later removed therefore survives in the `confirmations` set and still counts toward the `num_confirmations` threshold, allowing a request to execute with fewer live-member approvals than the configured threshold.

### Finding Description
The confirmation counting logic: [1](#0-0) 
increments/checks `confirmations.len()` against `self.num_confirmations` but never intersects `confirmations` with the live `self.members` set at confirmation- or execution-time.

The removal logic only cleans up requests where the removed member was the *original requester*, not confirmations they added to *other* requests: [2](#0-1) 

Specifically, `delete_member()` filters `self.requests` for `r.member == member` (line 365) and only removes confirmations for those requests (line 369). Any request originated by a different member, but confirmed by the member being deleted, is untouched — its `confirmations` `HashSet<String>` still contains the deleted member's `to_string()` entry.

`current_member()` only checks the *caller's* current membership at confirm time: [3](#0-2) 
It never re-validates the membership of *previously recorded* confirmations already stored in the `confirmations` map.

**Binding broken:** `confirmations.len()` (recorded approvals) is assumed to equal "approvals from members that are members" but in reality it can include approvals from accounts no longer in `self.members`. That is: `count(confirmations) == count(confirmations from live members)` is violated once a confirming (non-originating) member is deleted.

### Impact Explanation
This matches the Critical impact category "a multisig request executed below threshold." Consider `num_confirmations = 3` with 4 members {A, B, C, D}. Member A adds a sensitive request (e.g., `Transfer`, `AddKey`, `DeployContract`). B confirms (2 confirmations: A+B). Members then execute a separate, legitimate `DeleteMember` request removing B (e.g., because B's key was compromised or B left the organization) — `delete_member()` only clears requests where `r.member == B` (B's own originated requests), leaving B's confirmation on A's request intact. Now only A, C, D are live members (3), and the config still requires 3 confirmations. C confirms A's request: `confirmations.len()` is now 2 (A, B stale) + 1 (C) = 3, meeting `num_confirmations`, and the request executes — even though only 2 of the 3 confirmations (A, C) came from currently-live members, i.e., B (no longer a member, possibly a removed/compromised party) effectively still has an active vote toward moving funds, deploying code, or adding keys.

### Likelihood Explanation
This requires no privileged/foundation/attacker-external assumptions beyond the contract's own designed governance flow: any normal member-removal event (turnover, key rotation, compromise response) that occurs while another request is pending confirmation triggers this. Given that `DeleteMember` and other request confirmations are routine operational events for a multisig, encountering overlapping pending requests is a realistic and even likely occurrence, not a contrived edge case.

### Recommendation
When confirming or executing a request, filter `confirmations` to intersect with `self.members` (or re-validate each confirming identity's membership) before comparing against `num_confirmations`. Additionally, `delete_member()` should scan and strip the deleted member's `to_string()` entry from every entry in `self.confirmations`, not just from requests it originated.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A.add_request(sensitive_request)` → creates `request_id`, empty confirmations set.
3. `B.confirm(request_id)` → `confirmations = {A, B}` (2/3, per lines 300-314 in `multisig2/src/lib.rs`).
4. Separately, run and fully confirm a `DeleteMember{member: B}` request → `delete_member()` executes (lines 356-379), removing B from `self.members`, but request_id's confirmation set is untouched since `r.member` for `request_id` is `A`, not `B`.
5. `C.confirm(request_id)` → `confirmations.len() == 2` before insert, `2 + 1 >= 3` → threshold met → `execute_request` runs `sensitive_request`, even though only A and C are live confirming members and B's stale confirmation was the deciding vote.

### Citations

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
