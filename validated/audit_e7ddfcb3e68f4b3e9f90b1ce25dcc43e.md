I have enough context now to finalize my analysis of the `delete_member` function in `multisig2/src/lib.rs`.## Vulnerability Confirmed

The bug-class from the report (a state used for a security-critical count omits data that should invalidate/include additional entries, causing a threshold check to pass on stale/incomplete data) maps to `multisig2/src/lib.rs`'s member removal logic: **stale confirmations from a removed multisig member are never purged from *other* members' pending requests**, so they keep counting toward the `num_confirmations` threshold used to authorize fund transfers, key additions, and contract deployments.

### Title
Removed multisig member's stale confirmations still count toward execution threshold - (multisig2/src/lib.rs)

### Summary
`delete_member()` only deletes requests that the removed member *originally submitted* (`r.member == member`), but does not scan and purge that member's confirmation entries from requests submitted by *other* members. `confirm()` never re-validates that every account/key already present in a request's `confirmations` set is still a current member — it only checks that the *caller* is a member. As a result, a request can be executed using a headcount of confirmations that includes a party who is no longer a trusted member of the multisig.

### Finding Description
`confirm()` decides to execute a request purely by comparing the stored `confirmations` set size to `num_confirmations`: [1](#0-0) 

`delete_member()` is the only place that prunes state on member removal, and it filters `self.requests` by submitter (`r.member == member`), not by scanning `self.confirmations` values for a stale entry belonging to the removed member: [2](#0-1) 

Scenario:
1. Members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` calls `add_request` for a `Transfer`/`AddKey`/`FunctionCall` request `R` (submitter = A, `confirmations = {}`).
3. `D` calls `confirm(R)` while still a member → `confirmations = {D}` (1 < 3, stored not executed).
4. The group later executes a separate, legitimately-confirmed `DeleteMember { member: D }` request (e.g., because D's key was suspected compromised). `delete_member` only removes requests where `r.member == D` (i.e. requests *D itself* submitted) — request `R` was submitted by `A`, so its confirmations set `{D}` is left untouched even though `D` is now removed from `self.members`.
5. `B` confirms `R` → `confirmations = {D, B}` (2 < 3).
6. `C` confirms `R` → `confirmations.len() + 1 == 3 >= num_confirmations` → `execute_request(R)` runs.

`R` executes with only two *live* members (`B`, `C`) having actually approved it after `D`'s removal, plus one stale vote from a party who is no longer trusted. The equality that should hold —
`confirmations_counted_for_execution == confirmations_from_current_live_members`
— is broken: the left side includes an entry that no longer belongs to the right side's set.

### Impact Explanation
This directly matches the High/Critical impact category "a multisig request executed below threshold." The executed request can be any `MultiSigRequestAction`, including `Transfer` (moving NEAR out of the multisig-controlled account), `AddKey`/`AddMember` (granting new access), or `DeployContract` (arbitrary code takeover of the account) — i.e. concrete NEAR loss or unauthorized custody change is reachable using fewer live-member approvals than the multisig's own configured security threshold guarantees.

### Likelihood Explanation
This requires no privileged exploit action beyond the multisig's own documented lifecycle: a member confirms a pending request, is later legitimately removed (e.g., due to key compromise or offboarding — the very reason `DeleteMember` exists), and the remaining members continue routine `confirm()` calls unaware that a removed member's earlier vote is still silently counted. No redeploy, foundation intervention, or attacker-controlled deployment is needed; it is a straightforward operational sequence (add request → partial confirm → remove confirming member → remaining confirms reach the same numeric threshold).

### Recommendation
On `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests submitted by the removed member) and strip any entry matching the removed member's identity from every pending request's confirmation set. Alternatively, when checking the threshold in `confirm()`, filter `confirmations` to only those entries that are still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
Given `multisig2/src/lib.rs` semantics:
1. Deploy with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(R)` where `R = Transfer { amount, receiver_id: attacker_or_external }`.
3. `D.confirm(R)` → `confirmations[R] = {D}`.
4. Legitimately execute a `DeleteMember{member: D}` request (3 confirmations from A/B/C on a *different* request) — `delete_member` removes `D` from `self.members` but does not touch `confirmations[R]`.
5. `B.confirm(R)` → `confirmations[R] = {D, B}` (len 2).
6. `C.confirm(R)` → `len + 1 == 3 >= num_confirmations` → `execute_request(R)` fires, transferring funds/adding a key with `D`'s stale, now-invalid confirmation counted as one of the three required approvals. [1](#0-0) [2](#0-1)

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
