### Title
Removing a multisig member doesn't purge their stale confirmations on other pending requests, allowing execution below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` (and the analogous `DeleteKey` handling in `multisig/src/lib.rs`) only deletes outstanding requests that the removed member *created*, but never scans the `confirmations` map for confirmations the removed member cast on *other* members' requests. Those stale confirmations stay counted toward `num_confirmations` in `confirm()`, so a request can later execute with fewer live-member approvals than the configured threshold — an exact analog of the "bonus not subtracted" bug: a value (`confirmations.len()`) recorded at approval time diverges from the truth at settlement time (whether that approver is still a trusted member), and the divergence is never reconciled before the action fires.

### Finding Description
The custody binding that must hold is:
`confirmations.len()` counted in `confirm()` == number of confirmations from **currently live** members.

`confirm()` executes the request purely based on the size of the stored `HashSet<String>` of confirmations: [1](#0-0) 

When a member is removed, `delete_member` only cleans up requests keyed by that member as the *signer/creator* (`r.member == member`), and only clears `num_requests_pk`/deletes the access key: [2](#0-1) 

It never iterates `self.confirmations` to strip that member's `to_string()` entry from requests created by *other* members that this member had previously confirmed. Those entries remain in the `HashSet<String>` and are counted by `confirm()`'s `confirmations.len() as u32 + 1 >= self.num_confirmations` check.

The identical gap exists in the original `multisig/src/lib.rs`, where `DeleteKey` removes requests filtered by `r.signer_pk == pk` (the creator only) and clears `num_requests_pk`, but does not purge that key's confirmation from `self.confirmations` on requests it merely confirmed: [3](#0-2) 

### Impact Explanation
This is a Critical-severity impact per the custody binding: "a multisig request executed below threshold." A pending request can be executed (moving funds, adding/removing keys, deploying code, etc.) with fewer than `num_confirmations` confirmations from members who are still part of the multisig at execution time, because one or more of the counted confirmations belong to a member who has since been removed. This defeats the fundamental security guarantee of the K-of-N multisig scheme.

### Likelihood Explanation
This requires no external attacker, victim key, foundation privilege, or redeploy — it is triggered purely by the normal operational sequence of a multisig: (1) a member confirms a pending request without pushing it over threshold, (2) that member is later removed via a legitimate `DeleteMember`/`DeleteKey` request, (3) remaining members continue confirming the original pending request. This is a realistic and likely sequence for any multisig that rotates membership while requests are in flight (the default request lifetime allows requests to remain pending; `add_request`'s cooldown/`active_requests_limit` do not prevent this ordering).

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, and the `DeleteKey` branch in `multisig/src/lib.rs`), iterate `self.confirmations` for **all** requests (not just ones the member created) and remove the departing member's entry from each `HashSet`. Alternatively, when checking the threshold in `confirm()`, filter `confirmations` to only those entries whose corresponding member is still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
Members: `{A, B, C, D}`, `num_confirmations = 3`.
1. `B` calls `add_request_and_confirm(R)` → `confirmations[R] = {B}` (len 1).
2. `C` calls `confirm(R)` → `1 + 1 = 2 < 3` → `confirmations[R] = {B, C}` (len 2), request not yet executed.
3. Separately, `A`, `B`, `D` reach quorum on a `DeleteMember { member: C }` request and it executes: `delete_member` removes `C` from `members` and deletes `C`'s own outstanding requests, but does **not** touch `confirmations[R]`, which still contains `"C"`.
4. `D` calls `confirm(R)` → `confirmations[R].len() (2) + 1 = 3 >= 3` → `R` executes.

Result: `R` executed with approvals nominally `{B, C, D}`, but `C` was no longer a member at execution time — only `B` and `D` (2 live members) actually authorized it, one short of the configured `num_confirmations = 3`.

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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
