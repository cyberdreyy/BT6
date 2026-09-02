### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing requests to execute below the live-member quorum - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` only purges confirmations for requests that were *originated* by the removed member; it does not scrub that member's confirmations from other members' outstanding requests. `confirm()` later counts every entry in the `confirmations` set for a request without checking whether the confirming member is still a current member, so a "ghost" confirmation from a deleted member can still push a request over `num_confirmations` and cause it to execute.

### Finding Description
The multisig k-of-n security model is: `execute_request` should only run once `num_confirmations` *live* members have approved. The binding that must hold is:

```
count(confirmations for request R that come from current members) >= num_confirmations
```

`delete_member` breaks this binding. It removes outstanding requests *created by* the removed member, but leaves the removed member's confirmations on requests created by other members untouched: [1](#0-0) 

`confirm()` then trusts the raw size of the stored confirmations set with no re-validation that every entry still corresponds to a current member: [2](#0-1) 

`assert_valid_request` (called at the top of `confirm`) only validates that the *caller* is a current member; it never re-validates the historical entries already stored in `confirmations`: [3](#0-2) 

So the equality actually enforced by the code is `count(all stored confirmations, including ones from now-removed members) >= num_confirmations`, not the intended `count(live-member confirmations) >= num_confirmations`.

### Impact Explanation
This directly matches the "confirmations counted versus live members" custody boundary: a request (e.g. a `Transfer`, `AddKey`, or `DeployContract`) can be executed with fewer than `num_confirmations` *currently authorized* signers approving it, because one or more of the counted approvals come from a member who has since been deleted. Since `execute_request` can move funds (`Transfer`), add full-access keys (`AddKey`), or deploy new contract code (`DeployContract`), this is a multisig request executed below the intended threshold — a Critical-severity outcome per the custody model of the contract.

### Likelihood Explanation
No attacker privilege beyond the multisig's own normal operational sequence is required: (1) a member confirms a pending request without pushing it over threshold, (2) that member is later removed via a legitimate `DeleteMember` request, (3) remaining members continue confirming other, unrelated, already-pending requests. The stale confirmation silently persists and can tip the count over threshold with fewer live approvers than the configured `num_confirmations`, undermining the fundamental security guarantee of the contract without requiring any single member to act maliciously.

### Recommendation
When a member is removed, iterate all pending requests (not only ones authored by that member) and strip the removed member's entry from every `confirmations` set, deleting requests whose remaining confirmation count can no longer be reasoned about, or simply re-validate at `confirm()` time (and best at request-execution time) that every entry in the confirmations set still belongs to a current member before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy multisig with members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` calls `add_request` for a `Transfer` request `R` (no confirm).
3. `D` calls `confirm(R)` → `confirmations[R] = {D}` (1 of 3).
4. Members submit and confirm a separate `DeleteMember{ D }` request (3 confirmations from A, B, C) → executes; `D` removed, `members = {A, B, C}` (still `>= num_confirmations`). `R`'s confirmations are untouched because `delete_member`'s cleanup loop only matches requests where `r.member == D` (i.e., requests *authored* by D), not requests D merely confirmed: [4](#0-3) 
5. `A` calls `confirm(R)` → `confirmations[R] = {D, A}`, count = 2, still below 3, no execution yet.
6. `B` calls `confirm(R)` → `confirmations[R].len() as u32 + 1 = 3 >= num_confirmations` → `execute_request(R)` runs the `Transfer`, even though only 2 of the 3 current members (`A`, `B`) ever approved it — the third "confirmation" came from `D`, who is no longer a member: [5](#0-4)

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
