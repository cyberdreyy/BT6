## Analysis Result

### Title
Multisig `confirm()` counts stale confirmations from removed members, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm()` authorizes execution of a request purely by counting the size of the `confirmations` `HashSet` stored for that request, without verifying that each entry still belongs to a current multisig member. `delete_member()` only purges confirmations/requests *created by* the member being removed, not confirmations that member previously cast on *other* pending requests. As a result, a request can accumulate a confirmation from a member who is later removed, and that stale confirmation still counts toward `num_confirmations`, letting the request execute with fewer live-member approvals than the configured threshold.

### Finding Description
`confirm()` decides whether to execute a request solely based on the confirmation count: [1](#0-0) 

The check `confirmations.len() as u32 + 1 >= self.num_confirmations` treats every entry in the stored `HashSet<String>` as an equally valid, live approval. There is no re-validation that the accounts/keys represented by those stored strings are still members of `self.members` at execution time.

`delete_member()` removes a member and cleans up *only* the requests that member itself created: [2](#0-1) 

```rust
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```

This filter only matches on `r.member` — the *creator* of a request — it never scans the `confirmations` map for entries where the removed member had merely *confirmed* (but not created) someone else's pending request. Those stale confirmation strings remain in storage indefinitely and continue to count toward `num_confirmations` for any pending request they were attached to.

The custody-relevant binding here is: **confirmations counted (`confirmations.len()`) must equal live-member approvals**. This invariant is broken because a removed member's historical confirmation is never invalidated.

### Impact Explanation
An attacker-controlled or colluding scenario: a member confirms a pending request (e.g., a `Transfer` action moving NEAR out of the multisig, or an `AddKey`/`DeployContract` action granting control), is later removed from the multisig via a legitimate `DeleteMember` request, and the pending request they confirmed keeps their stale confirmation. Subsequent confirmations by remaining live members can then push the count to `num_confirmations` and trigger execution — even though the number of *currently valid* members who approved is one (or more) short of the configured threshold. This is exactly "a multisig request executed below threshold," moving NEAR (or granting keys/contract code deployment) without sufficient live authorization — a Critical-severity custody-binding violation.

### Likelihood Explanation
This does not require any extra privilege beyond normal multisig usage: any member can create/confirm requests, and member removal via `DeleteMember` is a normal, expected operation (e.g., rotating signers, revoking a compromised key). The scenario naturally arises whenever a member who has an outstanding confirmation on someone else's request is removed before that request finishes collecting confirmations — a routine sequence of events, not requiring any unusual timing or race condition.

### Recommendation
When removing a member in `delete_member()`, iterate over `self.confirmations` (not just `self.requests`) and strip the removed member's identifier from every pending request's confirmation set, not only from requests they created. Alternatively, `confirm()` should re-validate at execution time that every account/key in the stored confirmation set is still present in `self.members` before counting it toward the threshold.

### Proof of Concept
1. Deploy multisig with members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R`: `Transfer { amount: <all funds> }` to an attacker-controlled receiver (does not confirm).
3. `D` calls `confirm(R)` → `confirmations = {D}` (count 1).
4. Members separately create and confirm a `DeleteMember { member: D }` request (reaching the required 3 confirmations of *that* request) — `D` is removed from `self.members`; per `delete_member()` (lines 355-379) only requests created by `D` are cleaned up, so `R`'s confirmation set `{D}` is untouched.
5. `B` calls `confirm(R)` → `confirmations = {D, B}` (count 2).
6. `C` calls `confirm(R)` → count becomes 3, `3 >= num_confirmations`, so `execute_request` runs and the `Transfer` executes — despite only `B` and `C` being valid, live confirming members (2 < 3 threshold). [3](#0-2) [4](#0-3)

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
