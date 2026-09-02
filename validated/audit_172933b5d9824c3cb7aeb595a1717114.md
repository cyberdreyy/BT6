### Title
Multisig executes requests below the live-member threshold because stale confirmations from removed members are never purged - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations by the raw size of the stored `HashSet<String>` for a request, without verifying that every confirming identity is still a current member. `delete_member` only removes requests that the deleted member *created*; it never scans other pending requests' `confirmations` sets to strip the deleted member's stale confirmation. A request can therefore execute with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
The binding that must hold is: `confirmations_counted_toward_threshold == confirmations_from_current_members`. The contract violates this.

`confirm()` only checks that the *caller* is not already in the set, then compares the raw set size against the threshold: [1](#0-0) 

`delete_member()` removes the departing member from `self.members`, deletes any *requests they authored*, and clears their `num_requests_pk` entry — but it never iterates `self.confirmations` to remove that member's confirmation from other requests they had merely confirmed (not authored): [2](#0-1) 

Sequence:
1. `num_confirmations = 3`, members `{A, B, C, D}`.
2. `A` creates request `X` via `add_request_and_confirm`, which auto-confirms with `A`: `confirmations(X) = {A}`.
3. `D` (a member scheduled for removal) confirms `X`: `confirmations(X) = {A, D}` — still below threshold (2 < 3), so it stays pending.
4. A separate request removing `D` (`DeleteMember`) is confirmed and executed. `delete_member` removes `D` from `members`, but `X`'s stored confirmation set still contains `D`.
5. `C` (still a live member) confirms `X`. `confirm()` computes `confirmations.len() as u32 + 1 = 2 + 1 = 3 >= num_confirmations (3)`, so the request executes — with only `A` and `C` (2 live members) actually consenting, one short of the 3 required.

### Impact Explanation
This directly matches the Critical class "a multisig request executed below threshold." An arbitrary `MultiSigRequest` (transfer, `AddKey`/`FunctionCall`, further `DeleteMember`/`AddMember`) can be pushed through with fewer genuine confirmations than `num_confirmations` mandates, letting funds move or privileges change with less agreement than the deployment's security model promises.

### Likelihood Explanation
No privileged access is required beyond being a normal member with a legitimate confirm right; the only precondition is ordinary membership churn (removing a member who had confirmed a still-pending request), which is a routine, expected multisig operation, not an edge case. The bug is triggered purely by transaction ordering/timing available to any member, matching the "unprivileged attacker" baseline.

### Recommendation
When removing a member in `delete_member`, iterate all pending `requests`/`confirmations` entries and drop the removed member's entry from every confirmation set (not just requests they authored). Alternatively, when counting confirmations in `confirm()`, filter the stored set against `self.members` (i.e., recompute `confirmations.iter().filter(|m| self.members.contains(m)).count()`) before comparing to `num_confirmations`, so a request only executes with confirmations from currently valid members.

### Proof of Concept
See the sequence in "Finding Description": create request `X` with members A and D confirming (2/3), remove D via a `DeleteMember` request, then have C confirm `X`. `confirm()`'s check at [3](#0-2)  passes and `execute_request` runs with only 2 live-member approvals though `num_confirmations == 3`.

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
