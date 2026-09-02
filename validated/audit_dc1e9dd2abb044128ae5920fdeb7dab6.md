### Title
Multisig requests can execute below the configured confirmation threshold because removed members' stale confirmations are still counted - (File: multisig2/src/lib.rs)

### Summary
`delete_member` in the multisig2 contract removes a member from `self.members` and deletes only the requests that member *created*, but it never scrubs that member's confirmation votes cast on other pending requests. Those stale confirmations remain in the `confirmations: LookupMap<RequestId, HashSet<String>>` map and continue to count toward `num_confirmations` when a request is later confirmed by remaining members, allowing a request to execute with fewer live-member confirmations than the configured threshold.

### Finding Description
The intended invariant is: a request executes only when `num_confirmations` distinct **live** members have confirmed it, i.e. `count(confirmations ∩ current_members) >= num_confirmations`.

`confirm()` checks this using the raw stored set size, without validating membership of prior confirmers: [1](#0-0) 

`delete_member` only cleans up requests *created by* the removed member — it does not iterate other requests to strip that member's confirmations from their `confirmations` sets: [2](#0-1) 

Because `confirmations` is a `HashSet<String>` keyed by the member's serialized identity and is only touched in `add_request`, `remove_request` (on execution/deletion), and `delete_member`'s narrow cleanup (limited to requests owned by the removed member), a confirmation recorded by member `B` on a request created by member `A` survives `B`'s removal. When another live member later confirms that request, `confirmations.len() + 1 >= num_confirmations` can become true while one of the counted confirmations belongs to a non-member.

The equality that should hold is:
`recorded_confirmations_for_request == confirmations_from_currently_live_members`

After a `delete_member` call, this breaks: `recorded_confirmations_for_request > confirmations_from_currently_live_members` for any request that a removed member had previously confirmed but did not create.

The same root cause exists in the older `multisig` contract's `DeleteKey` action, which likewise only removes requests signed (created) by the deleted key, not confirmations that key cast on other pending requests: [3](#0-2) 

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `DeployContract`, or `AddKey`/`AddMember` action can be executed with effectively fewer live-member approvals than `num_confirmations` requires, because a stale confirmation from a just-removed member is silently counted as valid. This weakens the security guarantee the multisig is supposed to provide (funds/authority protected by K-of-N approval) without requiring any privileged action beyond the normal multisig workflow (removing a member is itself an authorized multisig action, and it is exactly this authorized action that introduces the vulnerability for other in-flight requests).

### Likelihood Explanation
This requires no attacker with special privileges — it can happen in the normal course of legitimate multisig operation: any time a member is removed while other requests are still pending and had already received that member's confirmation. Given that membership changes (offboarding an employee/key rotation) are a realistic and expected multisig lifecycle event, and multiple requests being confirmed concurrently is common, this is a plausible, not merely theoretical, scenario.

### Recommendation
When executing `delete_member` (and the `DeleteKey` action in `multisig`), iterate all pending requests' confirmation sets and remove the departing member's entry from each, not only from requests they created. Alternatively, validate membership of every entry in `confirmations` at `confirm()`-time (i.e., filter `confirmations` against `self.members` before comparing against `num_confirmations`) so stale entries can never count toward the threshold.

### Proof of Concept
Using `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`:
1. `A` calls `add_request(R)` (creates request `R`, confirmations = `{}`).
2. `A` calls `confirm(R)` → confirmations = `{A}` (1 < 3).
3. `B` calls `confirm(R)` → confirmations = `{A, B}` (2 < 3).
4. Separately, an already-quorum-approved request removes `B` via `DeleteMember { member: B }` → `delete_member` runs: `B` is removed from `self.members`; only requests *created by* `B` are purged — `R` (created by `A`) is untouched, so `R`'s confirmations set still contains `B`.
5. `C` (a live member) calls `confirm(R)`. The check `confirmations.len() as u32 + 1 >= self.num_confirmations` evaluates `2 + 1 >= 3` → true, and `R` is executed via `execute_request`, even though only `A` and `C` are currently valid live confirmers (2 live confirmations, not the required 3).

This demonstrates a request executing with confirmations below the configured live-member threshold, matching: [1](#0-0) [2](#0-1)

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
