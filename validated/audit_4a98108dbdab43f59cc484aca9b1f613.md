## Confirmed analog

`delete_member` in `multisig2/src/lib.rs` removes only the **outstanding requests initiated by** the deleted member, and removes the member from `self.members`, but it never scrubs that member's identity out of the `confirmations` set of *other* pending requests that member previously confirmed as an approver. [1](#0-0) 

`confirm` counts votes purely by set size, `confirmations.len() as u32 + 1 >= self.num_confirmations`, without re-validating that every entry in the stored `confirmations: HashSet<String>` still corresponds to a live member: [2](#0-1) 

This is exactly the bug class described in the report: an entity ("gauge"/member) is "killed" (removed via `DeleteMember`), but a quantity already counted toward a running total (vote weight/confirmation) that was contributed by that now-dead entity is not purged from the aggregate that gates execution (`totalWeightsPerEpoch`/`confirmations.len()`). The stale contribution keeps counting toward the threshold even though the member no longer exists, breaking the binding `confirmations counted == live members who confirmed`.

### Title
Stale confirmations from deleted multisig members are still counted toward the execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
When a multisig member is removed via `DeleteMember`, `delete_member` only deletes requests that member itself originated; it does not remove that member's confirmation entries from other pending requests they had previously confirmed as an approver. `confirm()` later counts `confirmations.len()` unconditionally against `num_confirmations`, so a request can execute with fewer *live* members having actually approved it than `num_confirmations` requires.

### Finding Description
`delete_member` at [3](#0-2)  filters and removes requests where `r.member == member` — i.e., requests *added by* the deleted member — and clears their confirmations. It does not scan `self.confirmations` for entries belonging to the deleted member on requests added by *other* members.

The `confirmations` map is keyed by `RequestId -> HashSet<String>` of member-string identities [4](#0-3) . `confirm()` simply checks whether the caller (a still-current member, validated via `current_member()`) has not already confirmed, then compares the *stored* set size + 1 against `num_confirmations`: [5](#0-4) 

Nowhere in this path is the stored `confirmations` set for a pending request filtered against `self.members` before the threshold comparison. So if member A confirms request X (added by member B) and is later deleted through a separate `DeleteMember` request, X's confirmation set retains A's stale entry. Any subsequent confirmations by live members can push `confirmations.len() + 1 >= num_confirmations` to true even though the number of *currently valid* members who confirmed is one less than intended.

This mirrors the report's root cause precisely: `totalWeightsPerEpoch` (an aggregate used to gate a distribution) is not decremented when the contributing entity (gauge) is killed, so a stale contribution keeps counting toward a threshold-driven action after the entity's authorization to contribute has been revoked.

### Impact Explanation
This breaks the multisig's core custody guarantee: "K live members must approve any given `receiver_id`/`actions` before it executes." A request can be executed with effectively `K-1` (or fewer) genuinely live-member confirmations, i.e., **a multisig request executed below threshold** — explicitly listed as Critical impact ("a multisig request executed below threshold"). This can authorize `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc. actions without the intended quorum of currently-authorized members, i.e., NEAR (or delegated authority) moved by a party set not entitled to make that decision under current membership.

### Likelihood Explanation
The precondition is realistic and does not require a redeploy, victim key, or social engineering beyond ordinary multisig operations: any member turnover (a common, expected multisig lifecycle event — e.g., an employee/partner leaving) that occurs while that departing member had outstanding confirmations on requests initiated by *other* members will trigger this. No special privilege beyond being a normal multisig member (past or present) is needed to have contributed the stale confirmation; the remaining members don't need to do anything wrong — they simply confirm as usual, unaware that a phantom vote is still counted.

### Recommendation
When removing a member in `delete_member`, iterate `self.requests`/`self.confirmations` and strip the departing member's string identity from every confirmation set (not only requests they authored), or re-validate confirmations against `self.members` at read time in `confirm()` before comparing against `num_confirmations`, i.e., count only confirmations whose member entries are still present in `self.members`.

### Proof of Concept
1. Initialize a `MultiSigContract` with members `{A, B, C}` and `num_confirmations = 3`.
2. `B` calls `add_request` to create request `X` (`receiver_id`, `Transfer` action). `A` calls `confirm(X)` — `confirmations[X] = {A}` (size 1, plus B's implicit signer role only applies via `add_request_and_confirm`; assume `B` also confirms via `add_request_and_confirm`, so `confirmations[X] = {A, B}`).
3. Members with threshold `>= 3` (e.g. `{A,B,C,D}`, `num_confirmations=3`) submit and 2-of-4 confirm request `X`, leaving it pending at 2 confirmations.
4. A separate governance action executes `DeleteMember { member: A }` (approved by the remaining threshold of members, none of whom inspect confirmation membership of unrelated pending request `X`). `delete_member` removes `A` from `self.members` but does **not** touch `confirmations[X]`, which still contains `A`.
5. Only one more *live* member, `C`, calls `confirm(X)`. `confirmations.len() (2, including stale A) + 1 = 3 >= num_confirmations (3)` → request `X` executes, even though only `B` and `C` (2 live members) ever actually approved it, one short of the required 3 live-member threshold.

### Citations

**File:** multisig2/src/lib.rs (L126-128)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
```

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
