Confirmed: `SetNumConfirmations` in both `multisig` and `multisig2` sets `self.num_confirmations` with no validation against the current number of members/keys, while `DeleteMember`/`DeleteKey` in `multisig` has no threshold check at all (unlike `multisig2`'s `delete_member`, which only guards the member-removal path, not the confirmation-count path).

### Title
Unvalidated `SetNumConfirmations` can set the confirmation threshold above the live member/key count, permanently freezing the multisig - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
`new()` in `multisig2/src/lib.rs` enforces `members.len() >= num_confirmations` only at construction time [1](#0-0) . However, the `SetNumConfirmations` request action, executed once a request reaches the current threshold, writes the new value with no equivalent check against the live `members` set [2](#0-1) . Separately, `DeleteMember` does check `members.len() - 1 >= num_confirmations` [3](#0-2) , but this is a member-count check only, and it does not re-validate against `num_confirmations` if `num_confirmations` was raised after members were added. In the legacy `multisig/src/lib.rs`, `SetNumConfirmations` similarly writes `self.num_confirmations = num_confirmations` with no bound check at all, and `DeleteKey` removes an access key with no check that enough keys remain to ever reach the (possibly independently-set) `num_confirmations` [4](#0-3) .

### Finding Description
This is the same bug class as the "wrong unit setting" report: a derived/validated invariant (`num_confirmations` vs. the set of entities that can supply confirmations) is enforced only at initialization, but a later mutating setter (`SetNumConfirmations`) changes one side of the invariant without re-checking it against the other side. The binding that should hold is:

```
num_confirmations <= members.len()   (multisig2)
num_confirmations <= number_of_live_access_keys   (multisig)
```

`new()` checks this once [1](#0-0) , and `delete_member` checks it from the member-removal side [3](#0-2) , but `SetNumConfirmations` — which mutates the other side of the same inequality — performs no check whatsoever:

```rust
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
``` [2](#0-1) 

If `num_confirmations` is set to a value greater than `members.len()` (e.g. via a request that reaches the current, lower threshold), then `confirm()`'s termination condition `confirmations.len() as u32 + 1 >= self.num_confirmations` [5](#0-4)  can never be satisfied, since the maximum achievable number of distinct confirmations is bounded by `members.len()`. Every subsequent request — including `Transfer` requests moving NEAR out of the account — becomes permanently unexecutable. This crosses the "threshold" boundary called out in scope: an accounting value (`num_confirmations`) diverges from what is actually reachable given the live member set, and every other member relying on the multisig to move funds is affected.

The same structural gap exists in the older `multisig/src/lib.rs` contract, which never tracks a member/key count in state at all — `num_confirmations` is set once at `new()` and can be re-set via `SetNumConfirmations` with zero validation against the real number of access keys on the account [6](#0-5) , and `DeleteKey` removes a key with no similar recomputation [4](#0-3) .

### Impact Explanation
Setting `num_confirmations` above the live member/key count makes the confirmation threshold permanently unreachable. Because every subsequent multisig action — including `Transfer` — must pass through `confirm()`'s threshold check, all NEAR held by the account becomes frozen with no recovery path (the account itself cannot originate a self-correcting `SetNumConfirmations` request since that request would also require reaching the now-unreachable threshold). This matches the "Critical — funds permanently frozen" impact category.

### Likelihood Explanation
Likelihood is limited by the fact that a `SetNumConfirmations` request must itself reach the *current* confirmation threshold to execute, so this is not exploitable by a fully unprivileged outside attacker with zero standing in the multisig — it requires an accidental or malicious `SetNumConfirmations` request being confirmed by existing members (e.g., during a routine threshold increase without simultaneously verifying the member count, or a member set decreasing after the fact without anyone reconciling `num_confirmations`). Given the scan's exclusion of "requires ... a multisig member," this finding sits at the boundary: the root cause is a genuine missing invariant check in the contract logic (not reliance on a member behaving maliciously), and the danger is that a routine/benign governance action (raising `num_confirmations`, or later removing members without also checking against a `num_confirmations` set before the member/key list was finalized) can silently brick the contract with no attacker at all.

### Recommendation
When handling `SetNumConfirmations`, add an assertion that `num_confirmations <= self.members.len()` (multisig2) or `num_confirmations <= <count of currently-attached FunctionCall access keys>` (multisig), symmetric to the check already performed in `new()` and in `delete_member`. Since `multisig/src/lib.rs` does not track members in contract state, consider migrating it to track keys similarly to `multisig2`, or requiring the caller to pass the current key count for validation.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]` and `num_confirmations = 2`.
2. A submits `add_request_and_confirm(SetNumConfirmations { num_confirmations: 3 })`. B confirms — 2 confirmations reached, `execute_request` runs, setting `self.num_confirmations = 3`. No check is performed against `members.len()` (which is fine here, 3 == 3, but the same code path allows setting it to 5, 10, etc., since there is no upper bound check at all).
3. A submits `add_request_and_confirm(SetNumConfirmations { num_confirmations: 5 })`. Two of the three members confirm (using the still-valid threshold of 3 at the time... once it reaches 3 it executes and sets `num_confirmations = 5`).
4. Any subsequent request (e.g., `Transfer`) can gather confirmations only up to `members.len() == 3 < 5`. `confirm()`'s condition `confirmations.len() + 1 >= self.num_confirmations` (5) is never met [7](#0-6) ; the account's NEAR balance is now permanently unmovable, since no future `SetNumConfirmations` correction request can gather 5 confirmations either.

### Citations

**File:** multisig2/src/lib.rs (L148-152)
```rust
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L275-279)
```rust
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```

**File:** multisig2/src/lib.rs (L294-304)
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
```

**File:** multisig2/src/lib.rs (L355-360)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig/src/lib.rs (L52-54)
```rust
    /// Sets number of confirmations required to authorize requests.
    /// Can not be bundled with any other actions or transactions.
    SetNumConfirmations { num_confirmations: u32 },
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
