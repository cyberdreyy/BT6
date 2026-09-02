## Title
Stale confirmations from removed multisig members remain counted toward the execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges pending requests that were *created* by the removed member; it never removes that member's *confirmations* recorded on other pending requests they merely approved. Since `confirm()` decides execution purely by counting `confirmations.len()` against `num_confirmations`, a request can later execute using a stale approval from a member who is no longer part of the multisig, i.e. it can execute with fewer live approvals than the configured threshold.

### Finding Description
`add_request`/`confirm` record approvals as a `HashSet<String>` of member identifiers keyed on `request_id`: [1](#0-0) 

When a member is removed, `delete_member` cleans up only requests where the removed member is the *submitter* (`r.member == member`); it does not scan or clean the `confirmations` map for entries where that member appears as a *confirmer* on requests submitted by someone else: [2](#0-1) 

Because `confirm()` simply reads `self.confirmations.get(&request_id)` and compares its length to `num_confirmations` without validating that every entry in the set still corresponds to a member currently present in `self.members`, a removed member's earlier approval is silently retained and still counted: [3](#0-2) 

This breaks the intended custody binding: **recorded confirmations for a request should equal live-member approvals** (`confirmations.len() == count of confirmations from current members`). After a `DeleteMember` action, that equality no longer holds for any request the removed member had confirmed but did not create.

### Impact Explanation
This is a multisig request executed below threshold, which is explicitly a Critical-severity outcome: the contract can transfer funds, deploy code, add/delete keys, etc., after receiving fewer live-member approvals than `num_confirmations` requires, because one "vote" is a ghost from a party the organization just revoked. This is structurally analogous to the original report's core defect — a recorded approval (signature/commitment) that isn't bound to the context that should invalidate it (here: current membership, there: the specific vault/version) so it can be reused/counted where it no longer should apply.

### Likelihood Explanation
No special privileges are needed beyond being a legitimate multisig member at some point: any member who confirms a pending request and is subsequently removed (e.g., due to key compromise, offboarding, or malicious behavior) leaves a live "confirmation credit" behind. Multisig membership changes (key rotation, offboarding a bad actor) are a normal, expected operational flow, making this reachable in ordinary usage, not a contrived edge case.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests created by the removed member) and strip the removed member's identifier from every confirmation set; alternatively, when tallying in `confirm()`, filter the stored confirmation set down to entries whose member is still present in `self.members` before comparing against `num_confirmations`. The same defect pattern exists in `multisig/src/lib.rs`'s `DeleteKey` handling, which should receive the same fix.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `B` submits `add_request` for a `Transfer` (or any) action (`request_id = R`).
3. `A` calls `confirm(R)` — `confirmations[R] = {A}` (1/3).
4. Members detect `A`'s key is compromised and submit/approve a separate `DeleteMember { member: A }` request through the normal 3-of-4 flow; `delete_member` runs, removing `A` from `members` and deleting `A`'s access key — but it does **not** touch `confirmations[R]`, since request `R` was submitted by `B`, not `A` (`multisig2/src/lib.rs:361-371`).
5. Now only `{B, C, D}` are live members (3 total, threshold still 3, satisfying `delete_member`'s own check at `multisig2/src/lib.rs:357-360`).
6. `C` calls `confirm(R)` → `confirmations[R] = {A, C}` (2/3).
7. `D` calls `confirm(R)` → `confirmations.len()+1 >= 3` is satisfied by `{A, C, D}`, so `execute_request` runs — even though `A`'s approval was revoked when `A` was removed, and only two live members (`C`, `D`) actually approved. [4](#0-3) [3](#0-2)

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
