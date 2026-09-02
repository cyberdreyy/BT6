### Title
Stale confirmations from a removed multisig member allow a pending request to execute below the intended live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` purges pending requests and `num_requests_pk` entries only for requests *authored* by the member being removed, but never scrubs that member's existing *confirmations* recorded on other members' pending requests. `confirm` later counts `confirmations.len()` without re-validating that each recorded confirmer is still a current member, so a request can reach `num_confirmations` and execute even though one or more of the counted confirmations belong to an account that is no longer part of the multisig.

### Finding Description
`confirm` treats the confirmation threshold purely as a count of strings stored in `self.confirmations`, with no check that those entries correspond to accounts still present in `self.members`: [1](#0-0) 

`delete_member` removes the departing member from `self.members`, deletes their access key, clears `num_requests_pk` for them, and deletes only the requests *they created* (`r.member == member`). It does not touch `self.confirmations` for requests created by other members that the departing member may have already confirmed: [2](#0-1) 

The only sanity check performed at deletion time is that the *remaining member count* stays `>= num_confirmations`: [3](#0-2) 

This check protects against `num_confirmations` exceeding the total member count, but it says nothing about confirmations already banked by a member who is about to be removed. Because `confirmations` is a plain set of member-string entries with no expiry or membership re-validation, a confirmation recorded by member `B` on request `R` (authored by `A`) remains valid and counted even after `B` is deleted from `self.members` via a separate, unrelated `DeleteMember` request.

Binding broken: `confirmations recorded on R == confirmations by currently-live members`. Before the exploit: `confirmations(R) ⊆ members`. After a member is removed without revalidating outstanding confirmations on requests they didn't author: `confirmations(R) ⊄ members`, yet `confirm` still treats `|confirmations(R)| + 1 >= num_confirmations` as sufficient to execute `R`.

### Impact Explanation
This falls squarely under the Critical impact category "a multisig request executed below threshold." A transfer, `FunctionCall`, `AddKey`, or `DeployContract` request can be executed with fewer *live* member confirmations than `num_confirmations` nominally requires, because one of the counted confirmations belongs to an account that has since been removed from the multisig. This can enable movement of NEAR funds, key changes, or contract redeployment on the multisig account with an effectively lower approval bar than the members intended when they set `num_confirmations`.

### Likelihood Explanation
The bug is triggered purely by ordinary, non-malicious multisig operation (no external attacker key/account is required, and no rule-violating privileged single actor is needed beyond the multisig's own normal governance flow of confirming requests and removing members): (1) a request is created and partially confirmed, (2) before it accumulates enough confirmations, the members separately vote to remove one of the confirmers (e.g., because that member's key was compromised or they left the organization — a legitimate, expected administrative action), and (3) the remaining members continue confirming the original pending request, unaware that one of its recorded confirmations is now stale. Since nothing in `confirm` or `delete_member` guards against this ordering, the window is real and requires no privileged bypass beyond standard multisig usage that any deployment of this contract is expected to perform (member rotation).

### Recommendation
When removing a member in `delete_member`, iterate over all pending requests and confirmation sets and strip any confirmation entries belonging to the removed member (not only requests the member authored). Alternatively, have `confirm`/`execute_request` re-validate, at execution time, that every entry in the stored `confirmations` set still corresponds to a current member of `self.members`, discarding stale ones from the count before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request_and_confirm(R)` where `R` is `Transfer { amount }` to some receiver — this auto-confirms with `A`, so `confirmations(R) = {A}`.
3. `B` calls `confirm(R)` — `confirmations(R) = {A, B}` (2 < 3, not yet executed), per `confirm` logic at [4](#0-3) .
4. Members create and confirm a separate `DeleteMember { member: B }` request (self-request, requires 3 confirmations from A, C, D). It executes, calling `delete_member`, which removes `B` from `self.members`, but `R`'s confirmation set is untouched because `R.member == A`, not `B`, per the filter at [5](#0-4) .
5. Now `self.members = {A, C, D}` (3 members) and `num_confirmations` remains `3`.
6. `C` calls `confirm(R)`. `confirmations(R).len() + 1 == 3 >= num_confirmations (3)`, so `R` executes and transfers funds — even though only `A` and `C` are confirmations from *current* members; `B`'s confirmation, from a now-removed account, was counted toward the threshold.

This demonstrates a multisig request executing with a confirmation count that includes a non-member, i.e., executed below the intended live-member threshold.

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
