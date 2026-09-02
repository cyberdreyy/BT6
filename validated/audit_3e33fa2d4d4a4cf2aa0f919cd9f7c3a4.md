### Title
`delete_member` in `multisig2` fails to purge a removed member's confirmations from requests it did not author, allowing a stale confirmation to count toward the `num_confirmations` threshold and execute a request with support from fewer live members than required - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only removes outstanding *requests* that the removed member itself authored, and only clears `confirmations` for those requests. It never scans other pending requests to strip a **confirmation** cast by the member being deleted. Because `confirm()` counts entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set without re-validating that every entry still belongs to a current member of `self.members`, a confirmation from an account/key that has since been removed from the multisig continues to count toward the `num_confirmations` threshold. This lets a request execute using fewer *live* member approvals than the configured threshold — the same class of bug as the reported `GameItems` issue, where a value tracked in one place (recorded confirmation count / recorded mint-burn count) is not kept in sync with the real, current state (live membership / real token balances) that it is supposed to represent.

### Finding Description
The relevant binding the multisig is supposed to enforce is:

```
number of confirmations from CURRENT members on a request >= num_confirmations
```

`confirm()` in `multisig2/src/lib.rs` checks this using the raw size of the `confirmations` `HashSet<String>` for the request: [1](#0-0) 

Membership can change at any time via `DeleteMember`, executed through `execute_request` -> `delete_member`: [2](#0-1) 

`delete_member` only cleans up requests **authored** by the removed member (`r.member == member`), removing those requests along with their confirmation sets. It does **not** search other pending requests for a confirmation cast *by* the member being deleted (i.e., where `member.to_string()` is a member of that request's `confirmations` HashSet but `r.member != member`). Consequently, a still-open request that was previously confirmed by the now-removed member retains that confirmation in its `HashSet<String>` forever, even though the confirming identity is no longer part of `self.members`.

When a later `confirm()` call from a genuinely current member pushes `confirmations.len() + 1 >= num_confirmations`, the request executes: [3](#0-2) 

The stale confirmation is counted exactly the same as a live one, so the request can be authorized with fewer *actual, current* member approvals than `num_confirmations` mandates. This is the same accounting-divergence bug class as the report: a count that should track a live/underlying state (member set / token supply) is updated only for a subset of paths (only the member's own authored requests / only `mintBatch`+`burnBatch` events tracked, not batch counters) and silently drifts from reality for the remaining paths (confirmations the member cast on *other* requests / `mintBatch`/`burnBatch` calls).

### Impact Explanation
This falls squarely under the "Critical" bucket defined by the rules: *"a multisig request executed below threshold."* A `Transfer`, `AddKey`/`FunctionCall`, or `DeployContract` request (including draining funds, upgrading the contract, or granting a full-access key) can be executed with fewer live-member approvals than the configured `num_confirmations`, effectively lowering the security threshold of the multisig without any of the remaining members realizing it.

### Likelihood Explanation
The precondition is realistic and requires no privileged access beyond normal, expected multisig usage: any member can confirm a pending request; membership changes (onboarding/offboarding signers, rotating a compromised key) are a normal and expected multisig operation described in the contract's own README. The only requirement is that a request remain open (not yet fully confirmed) at the time a confirming member is later removed — plausible for any request nearing, but not reaching, the confirmation threshold. No malicious "victim key," redeploy, or social engineering is required — a normal governance action (removing a member) combined with ordinary confirm/timing leaves stale state that any subsequent confirmer can exploit or unintentionally trigger.

### Recommendation
In `delete_member`, iterate over **all** pending requests (not just those authored by the member) and remove the deleted member's entry from each request's `confirmations` `HashSet<String>`. Alternatively, validate membership of every entry in the `confirmations` set at `confirm()` time (e.g., recompute the count by filtering the set against `self.members` before comparing to `num_confirmations`), so removed members can never contribute to reaching the threshold.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request(R)` (e.g., `Transfer` of contract funds to an attacker-controlled account). `R`'s `confirmations` set starts empty; `A` (the author) has not confirmed.
3. `B` calls `confirm(R)` → `confirmations = {B}` (len 1, < 3, not executed) — [1](#0-0) .
4. `D` calls `confirm(R)` → `confirmations = {B, D}` (len 2, < 3, not executed).
5. Separately, members `A`, `B`, `C` create and confirm a `DeleteMember { member: D }` request (3-of-3 confirmations from the then-current 4 members), which executes `delete_member(D)` — [2](#0-1) . This request was authored/related to `D`-removal, not to `R`, so the loop `self.requests.iter().filter_map(|(k,r)| if r.member == member ...)` does not touch `R` (its author is `A`, not `D`). `R`'s confirmation set `{B, D}` is left untouched. Members are now `{A, B, C}`, still satisfying `num_confirmations = 3`.
6. `C` calls `confirm(R)` → `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `R` (the `Transfer`) executes, even though only 2 of the 3 *current* members (`B` and `C`) actually approved it; the third counted "confirmation" belongs to `D`, who is no longer a member.

This demonstrates a `Transfer`/arbitrary request executed with confirmations from fewer live members than `num_confirmations` requires — an unauthorized move of funds/permissions below the configured threshold.

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
