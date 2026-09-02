### Title
Multisig executes a request below the K-of-N threshold using stale confirmations from removed members - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts entries in a request's `confirmations: HashSet<String>` against `num_confirmations` to decide whether to execute the request. When a member is removed via `delete_member`, that cleanup only purges requests *created* by the removed member; it never scans and purges that member's existing *confirmations* on requests created by other members. A confirmation recorded by an account that is later removed from `self.members` therefore keeps counting toward the K-of-N threshold, allowing a request (e.g. a `Transfer`, `DeployContract`, or `AddKey`) to execute with fewer live, currently-authorized signers than `num_confirmations` requires.

### Finding Description
The relevant invariant the contract must preserve is: `count(confirmations from accounts in self.members) >= num_confirmations` before `execute_request` is called. Instead, the code checks: [1](#0-0) 

`confirm()` only verifies that the *current* caller is a member via `assert_valid_request`/`current_member()` [2](#0-1) , but it never re-validates that the *previously stored* entries in `confirmations` still belong to members currently in `self.members`.

`delete_member` is the only place that mutates stored confirmations when a member is removed, and it only removes **requests whose creator (`r.member`) is the member being deleted**: [3](#0-2) 

It does not iterate `self.confirmations` to strip the removed member's account/key string from confirmation sets of *other* requests (i.e., requests created by someone else that this member had already confirmed). Those stale strings remain in the `HashSet<String>` for the request forever, and the plain `len() as u32 + 1 >= self.num_confirmations` check in `confirm()` cannot distinguish a stale confirmation from a live one.

### Impact Explanation
This breaks the multisig's core custody/authorization binding: "number of confirmations counted" must equal "number of confirmations from currently live/authorized members." Concretely:
1. Members `{A, B, C, D}`, `num_confirmations = 3`.
2. `C` creates request `R` (e.g., `Transfer` of contract funds) via `add_request`.
3. `B` confirms `R` → `confirmations = {B}`.
4. `D` confirms `R` → `confirmations = {B, D}` (2 < 3, not yet executed).
5. A separate, legitimately-approved multisig request removes `B` (`DeleteMember { member: B }`). `delete_member` only removes requests **created by** `B`; since `R` was created by `C`, `R`'s confirmation set `{B, D}` is untouched. Members are now `{A, C, D}`.
6. `A` confirms `R` → `confirmations.len() + 1 == 2 + 1 == 3 >= num_confirmations(3)` → `execute_request` fires and transfers funds.

Only `A` and `D` are actual live members who explicitly approved; `B`'s stale confirmation (from a since-removed member) was used to reach the threshold. This is a "multisig request executed below threshold" — a Critical-severity impact per the custody-binding classes in scope, since it permits movement of NEAR funds (or key/contract changes) with fewer authorized approvals than the configured `K` requires.

### Likelihood Explanation
No privileged/foundation/attacker-external action is required beyond normal multisig operations that members already perform routinely: creating requests, confirming, and removing members are all standard, unprivileged (from the contract's perspective, "member-privileged" but not "attacker" — any member, including a compromised or malicious one who can influence request ordering) operations. The scenario requires only ordinary sequencing (confirm before removal, execute after removal) and does not require exploiting any cryptographic weakness — it is a straightforward missing-cleanup bug. It also degrades gracefully into a benign case as long as members are never removed after they've confirmed pending requests, so it may go unnoticed in typical usage but is fully deterministic and reproducible once the removal happens while a confirmation is outstanding.

### Recommendation
When removing a member in `delete_member`, iterate all entries of `self.confirmations` (not just `self.requests` filtered by creator) and remove the member's identifier from every confirmation set, e.g.:
```rust
let request_ids: Vec<u32> = self.confirmations.keys().collect();
for request_id in request_ids {
    if let Some(mut confs) = self.confirmations.get(&request_id) {
        if confs.remove(&member.to_string()) {
            self.confirmations.insert(&request_id, &confs);
        }
    }
}
```
Alternatively (and more robustly), validate membership of each entry in `confirmations` at `confirm()` time before counting them (filter `confirmations` against `self.members` at threshold-check time), so stale entries never contribute to the K-of-N count regardless of how/when a member was removed.

### Proof of Concept
```rust
// members = [A, C_creator, B, D], num_confirmations = 3
let mut c = MultiSigContract::new(vec![A_member, C_member, B_member, D_member], 3);

// C creates a Transfer request R
as_member(C_member);
let request_id = c.add_request(MultiSigRequest {
    receiver_id: some_receiver(),
    actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
});

// B confirms
as_member(B_member);
c.confirm(request_id); // confirmations = {B}

// D confirms
as_member(D_member);
c.confirm(request_id); // confirmations = {B, D}, len=2 < 3, not executed

// Separately, members remove B via a fully-confirmed DeleteMember request
// (uses A, C, D confirmations legitimately) -> B removed from self.members,
// but R's confirmations set {B, D} is untouched because R was created by C, not B.

// A confirms R
as_member(A_member);
c.confirm(request_id);
// confirmations.len() (2, including stale "B") + 1 == 3 >= num_confirmations(3)
// -> execute_request fires the Transfer, even though only A and D are
//    live members who actually approved (2 of 3 required).
```

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
