## Title
Stale confirmations from deleted multisig members are still counted toward the confirmation threshold, allowing requests to execute below the required signer threshold — (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges outstanding **requests that the removed member created** (`r.member == member`), but never scans the `confirmations` map to strip that member's confirmation string from requests **created by other members**. Since `confirm()` counts confirmations purely by the size of the stored `HashSet<String>` without checking that each entry corresponds to a still-current member, a confirmation cast by a member who is later removed keeps counting toward `num_confirmations` forever. This lets a request execute with fewer live, currently-authorized signers than the configured threshold.

### Finding Description
`delete_member` in [1](#0-0)  removes:
- outstanding requests whose `MultiSigRequestWithSigner.member == member` (i.e., requests the deleted member *created*), along with their confirmations, and
- the deleted member's `num_requests_pk` entry and its membership/access key.

It never iterates `self.confirmations` to remove the deleted member's `to_string()` entry from confirmation sets belonging to requests created by *other* members. Those stale entries remain stored under `self.confirmations`.

`confirm()` at [2](#0-1)  determines whether to execute a request purely from the cardinality of the confirmations set:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
```
There is no check that every string in `confirmations` corresponds to a member still present in `self.members`. A confirmation recorded by a member that has since been deleted is indistinguishable from a confirmation by a currently valid member, so it silently continues to count.

This breaks the custody binding: `confirmations counted` should equal `confirmations from live members`, but after any `DeleteMember` action that removed a member who had confirmed (but not created) other pending requests, `confirmations counted > confirmations from live members` for those requests.

### Impact Explanation
This is a Critical-severity issue per the rules ("a multisig request executed below threshold"). A pending request created by a member other than the one being removed keeps its confirmations from the removed member. After removal, fewer *actual, currently-authorized* signers are needed to reach `num_confirmations`, so the multisig can approve and execute arbitrary actions (fund transfers, `AddKey`, `AddMember`, `DeployContract`, etc.) with less real authorization than intended — effectively lowering the security threshold without any explicit `SetNumConfirmations` action.

### Likelihood Explanation
This requires only ordinary multisig operation, no owner/foundation privilege beyond what any member already has: any group that (a) has a pending request confirmed by fewer than the full member set, and (b) later removes a member via the standard `DeleteMember` action (a routine operational action, e.g. off-boarding an employee or rotating a compromised key) will trigger this. No malicious deployment or redeploy is needed — it's a bug in ordinary lifecycle handling of `multisig2`.

### Recommendation
When removing a member in `delete_member`, iterate all pending requests' confirmation sets and remove the deleted member's string identifier from each, not just from requests the member itself created:
```rust
for (request_id, _) in self.requests.iter() {
    if let Some(mut confs) = self.confirmations.get(&request_id) {
        if confs.remove(&member.to_string()) {
            self.confirmations.insert(&request_id, &confs);
        }
    }
}
```
Alternatively, validate in `confirm()` that every entry in the confirmations set is still a current member before counting it toward the threshold.

### Proof of Concept
1. Deploy `MultiSigContract::new(members=[A, B, C, D], num_confirmations=3)`.
2. `A` calls `add_request(R)` for a `Transfer` (or `AddKey`) request — `R` is *not* auto-confirmed, `R.member == A`.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (len 1).
4. Members submit and confirm a separate `DeleteMember{member: B}` request (using A, C, D's confirmations) — this removes `B` from `self.members`, but since `B` did not create `R`, `confirmations[R] = {B}` is left untouched by `delete_member`.
5. Now live members are `{A, C, D}`, threshold is still 3.
6. `C` calls `confirm(R)`. Inside `confirm`, `confirmations.len()` is 1 (stale `B`) `+ 1 (C, about to insert)` `= 2 >= 3`? No — recompute with a smaller example to hit threshold exactly: use `num_confirmations = 2` for the same setup; then after step 3 (`B` confirmed, len=1) and step 4 (`B` removed), step 6 `C confirms`: `1 + 1 = 2 >= 2` → `execute_request(R)` fires immediately, even though only `C` is a genuinely live confirming member (`B`'s ghost confirmation is fabricated authorization) — the request executes with 1 live signer instead of the required 2. [1](#0-0) [2](#0-1)

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
