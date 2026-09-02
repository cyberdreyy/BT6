## Finding

### Title
Multisig requests can execute below the configured confirmation threshold because confirmations from deleted members are never purged from pending requests they did not author - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only removes pending requests that were *created* by the removed member, but leaves that member's `confirm()` votes intact on every other pending request they had confirmed. Because `confirm()` counts the size of the stored confirmation set against the static `num_confirmations` threshold, a stale confirmation from a member who is no longer part of the multisig continues to count toward execution, letting a request execute with fewer live-member confirmations than the configured `K`.

### Finding Description
The confirmation binding the contract is supposed to enforce is:
```
confirmations counted for a request == confirmations by accounts that are currently members
```
`confirm()` enforces the threshold purely by set size: [1](#0-0) 

`delete_member()` only cleans up requests **authored** by the removed member; it does not scan `self.confirmations` for entries where the removed member appears as a *confirmer* on requests authored by someone else: [2](#0-1) 

As a result, once a member is deleted, any confirmation they previously cast on a still-pending request (that they didn't create) remains in the `HashSet<String>` for that request and is still counted by `confirm()`'s `confirmations.len() as u32 + 1 >= self.num_confirmations` check. `current_member()` correctly refuses new confirmations from non-members, but the arithmetic threshold check has no notion of "is this stored confirmer still a member" - it just counts strings in the set.

### Impact Explanation
This breaks the K-of-N custody guarantee that all downstream systems (owners, auditors, other members) rely on: a `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` request can be executed by the contract account with fewer than `num_confirmations` *live* member approvals. Per the rules this is Critical impact - "a multisig request executed below threshold" - and can directly result in NEAR (or any asset controlled by the multisig account) being moved by parties that never should have reached quorum, e.g. after a compromised member's key/account is revoked specifically to stop them from participating further.

### Likelihood Explanation
No privileged action is required beyond normal multisig operation: any member can confirm a pending request as part of ordinary usage. Member removal (`DeleteMember`) is a standard, expected admin action (e.g., revoking a departing employee or a compromised key). The vulnerable state arises naturally whenever a member is removed while they have outstanding confirmations on requests they did not author themselves - a very plausible real-world sequence, and one an attacker (a member about to be removed, e.g. after key compromise is detected) can intentionally set up by front-loading confirmations on a malicious `Transfer` request before removal.

### Recommendation
When deleting a member in `delete_member`, iterate all pending requests' confirmation sets (not just requests authored by that member) and remove the member's entry from each. Alternatively, re-validate at `confirm()` time (and before executing) that every entry in the stored confirmation set still corresponds to a current member, discounting stale entries from the threshold count.

### Proof of Concept
1. Initialize `MultiSigContract::new` with 4 members (A, B, C, D) and `num_confirmations = 3`.
2. Member A calls `add_request` with a `Transfer` action sending the account's NEAR balance to an attacker-controlled account, then `confirm` (1 confirmation).
3. Member B (compromised/malicious) calls `confirm(request_id)` (2 confirmations: {A, B}).
4. The group detects B is compromised and submits/confirms a `DeleteMember { member: B }` request through the normal multisig flow; `delete_member` executes, removing B from `self.members`, but does **not** touch the confirmation set `{A, B}` on the transfer request from step 2 because that request's `member` (author) is A, not B - see `delete_member`'s filter `r.member == member` at [3](#0-2) .
5. Member C calls `confirm(request_id)` on the original transfer request. `confirmations.len()` is 2 (`{A, B}`) `+ 1 = 3 >= num_confirmations (3)` — the request executes and the transfer is sent, even though B is no longer a member and only 2 live members (A and C) actually approved it.

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
