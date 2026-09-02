## Title
Confirmations from a removed multisig member remain counted toward the execution threshold, allowing a request to execute below the required live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges requests that were *created by* the removed member, but never scrubs that member's confirmation entries from the `confirmations` map for requests created by *other* members. As a result, a stale confirmation from an account/key that has since been removed from `members` still counts toward `self.num_confirmations` in `confirm`, letting a request execute with fewer live-member approvals than the configured threshold.

### Finding Description
`confirm` decides whether to execute a request purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set: [1](#0-0) 

Confirmation membership is validated only at the moment of confirming (`current_member()` must be a current member at that time). Nothing re-validates the *existing* confirmations set for staleness after the fact.

When a member is removed via `delete_member`, the cleanup logic is: [2](#0-1) 

This only removes requests whose `r.member == member` (i.e., requests *added* by the removed member) and deletes their confirmation sets. It does **not** iterate over `self.confirmations` to strip the removed member's identifier from confirmation sets of requests added by *other* members. Those confirmation `HashSet<String>` entries are keyed by `member.to_string()` (an account id or public key), so a removed member's prior confirmation silently remains in place for any request they confirmed but did not create.

Consequently the binding "confirmations counted == live members who approved" is broken: `confirmations.len()` can include identifiers for members no longer present in `self.members`.

### Impact Explanation
This matches the Critical impact category "a multisig request executed below threshold": an attacker (or a group that mistakenly trusts a since-revoked key/account) can get a `Transfer`, `FunctionCall`, `AddKey`, etc. request executed using fewer *live* member confirmations than `num_confirmations` requires, because one of the counted confirmations belongs to an account/key that has already been removed from the multisig. This directly undermines the K-of-N custody guarantee the contract is supposed to enforce for the funds/keys held by the multisig account.

### Likelihood Explanation
No privileged capability beyond normal multisig membership is required. The scenario arises naturally whenever:
1. A member confirms a pending request but the request does not yet reach threshold.
2. That member is later removed (e.g., because their key was suspected compromised, or as normal membership rotation) via `DeleteMember`.
3. Remaining members confirm the still-pending request, unaware that a revoked confirmation is still silently counted.

This is a realistic, no-extra-privilege operational sequence (rotate-out-a-member while a request is in flight), not a contrived edge case, making it highly reachable.

### Recommendation
When removing a member in `delete_member`, iterate all requests (not just ones added by that member) and remove the member's identifier from each request's confirmation `HashSet`. Alternatively, validate at `confirm`-time (before counting) that every entry in the stored confirmation set still corresponds to a current `self.members` entry, discarding stale ones from the count.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A` calls `add_request_and_confirm(Transfer{amount})` → `confirmations[req] = {A}`.
3. `C` calls `confirm(req)` → `confirmations[req] = {A, C}` (2/3, not yet executed).
4. Members submit and confirm a `DeleteMember{member: C}` request (reaching 3-of-4 with A, B, D), executing `delete_member(C)`. `C` is removed from `self.members`; `self.confirmations` for `req` is untouched because `req.member == A`, not `C`.
5. `B` calls `confirm(req)` → `confirmations[req] = {A, C, B}`, length 3 `>= num_confirmations (3)` → `execute_request` fires, transferring funds.
6. Only two currently-live members (`A`, `B`) actually authorized this transfer at confirmation time; `C`'s stale confirmation, from a member removed in step 4, was still counted, so the transfer executed below the effective live-member threshold. [3](#0-2)

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
