### Title
Stale confirmations from deleted multisig members remain counted toward the confirmation threshold, allowing a request to execute with fewer live-member approvals than `num_confirmations` - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges confirmations for requests that were *originated* by the removed member. It does not scrub that member's confirmation entries from other pending requests that the member had *confirmed* (but not created). Because `confirm()` counts confirmations purely by set size (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without re-validating that every entry in the stored `HashSet<String>` still belongs to `self.members`, a request can be executed using a confirmation from an account/key that is no longer a member of the multisig — i.e., with strictly fewer *live* approvals than the configured `num_confirmations` threshold.

### Finding Description
The confirmation-count invariant the contract is supposed to enforce is:
`live confirmations on a request >= num_confirmations` before `execute_request` runs.

`confirm()` in `multisig2/src/lib.rs` (lines 292-315) implements this by comparing the size of the `confirmations` `HashSet<String>` associated with the request against `self.num_confirmations`: [1](#0-0) 

The set is populated by `member.to_string()` values of whoever calls `confirm`, gated only at call-time by `current_member()` returning `Some` (i.e. the *caller* must currently be a member) — see `assert_valid_request`: [2](#0-1) 

However, when a member is removed via `DeleteMember`, cleanup in `delete_member` only removes confirmation state for requests where `r.member == member` (i.e., requests the removed member *originated* via `add_request`): [3](#0-2) 

It never scans the `confirmations` map to strip the removed member's string entry from *other* requests they had confirmed as a non-originating approver. Those stale entries remain in the `HashSet<String>` for those other requests, and are counted by `confirm()`'s `confirmations.len()` comparison indefinitely — even though `self.members` no longer contains that member.

### Impact Explanation
This breaks the exact custody binding the rules call out: "confirmations counted versus live members." A request (including `Transfer`, `AddKey`, `FunctionCall`, `DeployContract`, etc.) can reach the execution threshold and be executed by `execute_request` while the number of currently-valid, live members who approved it is strictly less than `num_confirmations`. This is equivalent to a multisig request executed below threshold — one of the explicitly listed Critical impacts (funds moved / actions authorized by fewer approvals than the security model guarantees).

### Likelihood Explanation
The only precondition is an ordinary lifecycle event: a pending request R1 is confirmed (but not fully executed) by member M as a non-originating approver, and subsequently M is removed from the multisig via a separate `DeleteMember` request before R1 accumulates enough *live* confirmations. This is a normal governance action (membership rotation) that does not require any privileged misuse beyond the multisig's own designed operations — any deployment that rotates/removes a member while other requests are outstanding is exposed. No collusion beyond the multisig's own normal confirm/delete flow, no owner/foundation involvement, and no external contract is needed.

### Recommendation
When removing a member in `delete_member`, iterate over all entries in `self.confirmations` (not only requests originated by the removed member) and remove the member's string key from every confirmation set. Alternatively, change `confirm()` to recompute the *live* confirmation count by filtering the stored set against `self.members.contains(...)` before comparing to `self.num_confirmations`, e.g.:
```rust
let live_confirmations = confirmations
    .iter()
    .filter(|m| self.members.contains(&MultisigMember::from_string(m)))
    .count() as u32;
if live_confirmations + 1 >= self.num_confirmations { ... }
```

### Proof of Concept
1. Deploy multisig with members `{A, B, C, D}`, `num_confirmations = 3`.
2. `D.add_request(R1)` → `confirmations[R1] = {}`.
3. `A.confirm(R1)` → `confirmations[R1] = {A}`.
4. `B.confirm(R1)` → `confirmations[R1] = {A, B}` (2 confirmations, not yet ≥ 3).
5. Separately, members confirm and execute a `DeleteMember { member: B }` request (`add_request_and_confirm` + enough confirms). `delete_member` (`multisig2/src/lib.rs:355-379`) removes `B` from `self.members` and only clears confirmations for requests *originated* by `B`; `R1` (originated by `D`) is untouched, so `confirmations[R1]` still contains `B`.
6. `C.confirm(R1)`: `current_member()` succeeds (`C` is still valid), `confirmations[R1].len() + 1 = 3 >= num_confirmations(3)` → `execute_request(R1)` runs.
7. Result: R1 executes with confirmations `{A, B, C}`, but `B` is no longer a member — only 2 live members (`A`, `C`) actually approved it, one short of the configured 3-of-4 threshold.

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
