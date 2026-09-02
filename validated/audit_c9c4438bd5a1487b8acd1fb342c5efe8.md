### Title
Stale confirmations from removed multisig members remain counted toward the confirmation threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` only purges requests that were *originated* by the removed member; it never scans the `confirmations` map to strip that member's approval from requests originated by *other* members. As a result, a confirmation cast by a member who is later removed from the multisig continues to count toward `num_confirmations` on any pre-existing request they had confirmed but did not create, allowing that request to execute with fewer live-member approvals than the current threshold requires.

### Finding Description
The binding the multisig is supposed to enforce is:

`confirmations.len() (counted in confirm()) == number of currently-live members who have approved this request`

`confirm()` only checks that the *new* confirming caller is a live member via `current_member()`/`assert_valid_request`, and then compares the size of the stored `confirmations: HashSet<String>` against `self.num_confirmations`: [1](#0-0) 

There is no re-validation of the *previously stored* entries in that `HashSet` when a member is removed. `delete_member` is the only path that mutates state on membership removal, and it only removes requests whose *creator* (`r.member`) equals the deleted member — it does not touch `confirmations` for requests created by someone else: [2](#0-1) 

So if member `A` confirms a request created by member `B` (adding `A`'s string identity to that request's `confirmations` set), and `A` is subsequently removed via a legitimate `DeleteMember` request, `A`'s confirmation entry for `B`'s request is never purged. When the remaining live members continue to confirm, `A`'s stale confirmation is still counted toward `self.num_confirmations`, effectively letting the request execute with one fewer live-member approval than intended.

This is directly analogous to the reported bug class: a value that should be re-validated against current, live state (member confirmations) is instead treated as a permanently-cached fact once recorded, letting an authorization check be bypassed after the underlying authority (membership) changes.

### Impact Explanation
This is a Critical-severity issue per the rules: it allows a multisig request (including `Transfer`, `AddKey`, `AddMember`, `DeployContract`, etc.) to be executed with fewer than the required number of confirmations from *currently authorized* members, i.e. "a multisig request executed below threshold." An attacker (or a member who is later removed for cause, e.g. a compromised or malicious key) can pre-confirm a pending request before being removed, guaranteeing their approval survives and reduces the number of additional confirmations subsequently needed — effectively moving NEAR or granting access with a diminished live quorum.

### Likelihood Explanation
The bug requires an attacker/malicious member to be a legitimate multisig member at the time they confirm a request, and for that confirmation to survive their later removal. This is a very plausible scenario in practice: multisigs commonly rotate/remove members (key compromise, employee offboarding, etc.), and any outstanding request they had previously confirmed silently retains their approval. No special privileges beyond normal membership (which the design already grants to `k`-of-`n` participants) are needed to plant the stale confirmation.

### Recommendation
When executing `DeleteMember`, iterate over all entries in the `confirmations` map (not just requests originated by the removed member) and remove the deleted member's identity from every confirmation set, e.g.:
```rust
for (request_id, mut confs) in self.confirmations.iter() {
    if confs.remove(&member.to_string()) {
        self.confirmations.insert(&request_id, &confs);
    }
}
```
Alternatively, revalidate at `confirm()` time by filtering `confirmations` against `self.members` before comparing count to `self.num_confirmations`, so removed members' stale approvals never count.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `B` calls `add_request` to create a `Transfer` request `R` (request originator = `B`).
3. `A` calls `confirm(R)` → `confirmations(R) = {A}` (len 1, calls the code path at `multisig2/src/lib.rs:299-314`).
4. Members legitimately execute a separate multisig request to `DeleteMember { member: A }` (3-of-4 confirm it, per `multisig2/src/lib.rs:356-379`). Note this delete only removes requests where `r.member == A`; since `R` was created by `B`, `R` and its confirmations survive untouched.
5. `A` is no longer in `self.members`, so `current_member()` for `A` now returns `None` — `A` can no longer act.
6. `C` calls `confirm(R)` → `confirmations(R) = {A, C}` (len 2).
7. `D` calls `confirm(R)` → `confirmations(R).len() + 1 == 3 >= num_confirmations(3)` → `execute_request` fires the `Transfer`.

The transfer executes with only `C` and `D` — two currently-live members — actually approving after `A`'s removal, one short of the intended 3-of-4 live-member threshold, because `A`'s stale confirmation from before removal was still counted.

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
