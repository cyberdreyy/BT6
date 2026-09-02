I found a valid analog. In the multisig contract, deleting a member does not purge that member's confirmations from requests they did not originally submit, allowing stale confirmations from removed members to still count toward the execution threshold.

### Title
Stale confirmations from deleted members remain counted toward `num_confirmations`, allowing execution below live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`delete_member` in `multisig2/src/lib.rs` only removes pending requests and confirmation sets for requests that were *originally submitted* by the member being deleted. It does not scan and purge that member's `confirm()` entries from the `confirmations` set of *other* pending requests submitted by different members. As a result, a member who confirmed a request and is later removed from the multisig still counts toward the `num_confirmations` threshold for that pre-existing request, allowing the request to execute later with fewer live (currently authorized) confirmers than `num_confirmations` requires.

### Finding Description
`confirm()` adds the calling member's identity string into a `HashSet<String>` stored per `request_id`: [1](#0-0) 

When execution of `DeleteMember` happens, `delete_member` cleans up only requests where the request's *submitter* (`r.member`) equals the deleted member — it does not touch the `confirmations` HashSet entries belonging to that member on any other request: [2](#0-1) 

The binding that should hold is: `confirmations counted toward num_confirmations` == `confirmations from members who are still in self.members`. Concretely:
- Before: `members = {A, B, C, D}`, `num_confirmations = 3`. Member `A` submits request `R1` (`add_request`, unconfirmed). `B` calls `confirm(R1)` → `confirmations[R1] = {B}`. `C` calls `confirm(R1)` → `confirmations[R1] = {B, C}` (2 < 3, not executed).
- A separate `DeleteMember{member: C}` request reaches threshold and executes. `delete_member(C)` only removes requests whose `r.member == C` (i.e., requests *submitted* by C). Since `R1` was submitted by `A`, `R1`'s confirmations set `{B, C}` is left untouched, even though `C` is no longer in `self.members`.
- After: `members = {A, B, D}`. `D` calls `confirm(R1)`: `confirmations[R1].len() + 1 = 3 >= num_confirmations (3)` → `execute_request(R1)` runs.

`R1` executes with confirmations from `{B, C, D}`, but `C` is no longer a member at execution time — only `B` and `D` are live, authorized confirmers. The multisig's core invariant ("K live members must approve every request") is broken: the request executed with only 2 live confirmations against a threshold of 3.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." Any action type (`Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.) can be smuggled through with fewer live signers than configured, including transferring NEAR out of the multisig account or granting a full access key, effectively letting a minority of current members (plus a stale confirmation from a since-removed member) move funds or take over the account without meeting the intended K-of-N threshold.

### Likelihood Explanation
The preconditions are realistic in normal multisig operation: membership changes over time (turnover, key rotation, compromised-key removal) are an expected, routine multisig action, and pending unconfirmed/partially-confirmed requests commonly persist across such changes (default `active_requests_limit` is 12 outstanding requests, and requests are only auto-expired via the 15-minute `delete_request` cooldown path, which is opt-in). No special privilege is needed beyond being one of the K live members needed to push the stale-tainted request over threshold — an attacker/colluding minority just needs the request to remain pending across a membership change.

### Recommendation
When executing `DeleteMember`, iterate over all pending requests' `confirmations` sets (not just requests submitted by the deleted member) and remove the deleted member's identity string from each. Alternatively, validate at `confirm()`/execution time that every entry in the `confirmations` set for a request is still contained in `self.members`, discarding stale entries (and recomputing whether the threshold is actually met by live members) before executing.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. As `A`, call `add_request` with a `Transfer` request `R1` (do not confirm as A yet, or add via `add_request_and_confirm` and use a 5th non-submitting member scheme — either way ensure submitter of `R1` is not the member to be removed).
3. As `B`, call `confirm(R1)` → `confirmations[R1] = {B}`.
4. As `C`, call `confirm(R1)` → `confirmations[R1] = {B, C}` (2 confirmations, below threshold 3).
5. Submit and confirm (via 3 live members, e.g. A, B, D, on a separate request) a `DeleteMember{member: C}` action; it executes, removing `C` from `self.members`, per `delete_member` logic at [3](#0-2) . Note `R1` is untouched because `R1.member == A != C`.
6. As `D`, call `confirm(R1)`. `confirmations[R1].len() + 1 == 3 >= num_confirmations (3)` triggers `execute_request(R1)` at [4](#0-3) , transferring the funds even though only `B` and `D` are currently live members who approved — `C`'s stale confirmation counted toward the 3-of-4 threshold. [2](#0-1) [1](#0-0)

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
