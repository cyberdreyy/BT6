I've confirmed the vulnerability. `delete_member` at `multisig2/src/lib.rs:356-379` only removes requests *created by* the deleted member (filtered by `r.member == member`), and clears `num_requests_pk` for that member — but it never scans the `confirmations` map to strip that member's confirmation entries from *other* outstanding requests they confirmed but didn't create. This breaks the binding "confirmations counted == confirmations from live members." [1](#0-0) [2](#0-1) 

### Title
Stale confirmations from removed multisig members still count toward the approval threshold, allowing a request to execute below the live-member threshold - (File: multisig2/src/lib.rs)

### Summary
In `MultiSigContract`, when a member is removed via `DeleteMember` (handled by `delete_member`), the contract only deletes outstanding *requests created by* that member and their `num_requests_pk` entry. It does not remove that member's existing *confirmations* recorded against requests created by other members. `confirm` later counts `confirmations.len() + 1 >= self.num_confirmations` without re-validating that every stored confirmation still belongs to a current member. As a result, a confirmation cast by an account that has since been removed from the multisig continues to count toward the threshold, letting a request execute with fewer *live* approving members than `num_confirmations` requires.

### Finding Description
`confirm` at `multisig2/src/lib.rs:292-315` fetches `self.confirmations.get(&request_id)`, checks that the *current* caller hasn't already confirmed, and then compares the *stored set's size* against `num_confirmations` to decide whether to execute the request:
```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
} else {
    confirmations.insert(member.to_string());
    ...
}
```
It never re-checks that the members recorded in `confirmations` are still present in `self.members`.

`delete_member` at `multisig2/src/lib.rs:355-379` is the only place that prunes stale request/confirmation state on membership removal, and it is scoped exclusively to requests where `r.member == member` (i.e., requests *created* by the removed member):
```rust
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
self.num_requests_pk.remove(&member.to_string());
self.members.remove(&member);
```
It does not iterate `self.confirmations` to strip the removed member's string key out of confirmation sets belonging to requests created by *other* members. Consequently, any request that member X confirmed while still a member keeps X's confirmation entry in the `HashSet<String>` even after X's membership is deleted via a subsequent `DeleteMember` execution.

The binding that should hold is: `confirmations counted toward num_confirmations == confirmations from accounts currently in self.members`. After a member is removed, this equality breaks — the recorded count can include confirmations from accounts no longer in `self.members`.

### Impact Explanation
This is a Critical-class issue per the custody/authorization binding: a multisig request (e.g., `Transfer`, `AddKey`, `FunctionCall`) can be executed with contributions from fewer *live* members than the configured `num_confirmations` threshold, because a stale confirmation from a removed member is counted as if it were valid. This is exactly the "a multisig request executed below threshold" class — funds or privileged actions (adding a full-access key, transferring NEAR, deploying new contract code) can be authorized without the intended quorum of currently trusted members.

### Likelihood Explanation
Exploitation requires a realistic sequence with no special privileges beyond normal multisig usage:
1. Member X (still valid) confirms request R created by another member, leaving R one confirmation short of `num_confirmations`.
2. Later, the members execute a separate, legitimate `DeleteMember { member: X }` request (e.g., X leaves the organization, key rotation, or is compromised and removed).
3. R still exists with X's stale confirmation intact since `delete_member` never touched it.
4. A new/remaining member confirms R, and the confirmation count (which includes X's stale entry) reaches `num_confirmations`, causing execution — even though X is no longer a trusted member and did not (and cannot) reconfirm after removal.

This does not require any privileged foundation/owner action beyond the standard multisig confirmation flow, and does not rely on ignoring documented initialization; it is a normal, expected sequence of `add_request` → partial `confirm` → `DeleteMember` execution → further `confirm`.

### Recommendation
In `confirm`, before comparing the confirmation count to `num_confirmations`, filter the stored confirmation set down to entries whose corresponding member (parsed back from the string key) is still present in `self.members`, and use that filtered count. Alternatively, in `delete_member`, iterate all entries in `self.confirmations` (not just requests created by the removed member) and remove the deleted member's key from every confirmation set, decrementing effectively.

### Proof of Concept
1. Deploy `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. Member A calls `add_request` to create request R (e.g., `Transfer` to some receiver), signed as A (counts as A's implicit involvement only as creator, not confirmer).
3. Member B calls `confirm(R)` → confirmations = `{B}` (1/3).
4. Member C calls `confirm(R)` → confirmations = `{B, C}` (2/3, request not yet executed).
5. Separately, members execute a `DeleteMember { member: C }` request (reducing membership to `[A, B, D]`, still `>= num_confirmations`). `delete_member` removes C from `self.members` and `num_requests_pk`, but request R still exists with confirmations `{B, C}` untouched because R was created by A, not C.
6. Member D calls `confirm(R)` → `confirmations.len() (2, including stale C) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires the `Transfer`.
7. Result: R executed with real approvals from only B and D (2 live members) plus a stale confirmation from removed member C — one fewer live-member approval than the configured 3-of-4 threshold requires.

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
