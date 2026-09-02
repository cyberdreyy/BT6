## Title
Confirmations From Removed Multisig Members Remain Counted Toward Threshold - (File: `multisig2/src/lib.rs`)

### Summary
`multisig2`'s `delete_member` only purges requests and confirmations that were *created* by the removed member, but it never scans the `confirmations` map for entries where the removed member had *confirmed* someone else's still-pending request. Those stale confirmations remain in the `HashSet<String>` and continue to count toward `num_confirmations` in `confirm()`, breaking the binding "confirmations counted == confirmations by live members."

### Finding Description
`confirm()` determines whether a request executes purely by set size: `if confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0) . Membership is checked only for the caller invoking `confirm`, via `current_member()` [2](#0-1) ; the set is never re-validated against the current `members` set as a whole.

`delete_member` cleans up only requests whose `member` field (the *creator*) equals the deleted member, and removes that member's `num_requests_pk` entry and its own membership record: [3](#0-2) 
It does not iterate `self.confirmations` to strip the deleted member's identity from confirmation sets of *other* pending requests that the member had previously confirmed but did not create.

Consequence: if member `M` confirms request `R` (created by someone else) and is later removed from the multisig via `DeleteMember`, `M`'s stale confirmation string remains stored under `confirmations[R]`. When the remaining members later confirm `R`, `M`'s now-invalid confirmation is still counted in `confirmations.len()`, letting `R` execute with fewer *live* confirmations than `num_confirmations` requires.

### Impact Explanation
This breaks the core custody guarantee of the multisig: "a request executed" should require `num_confirmations` confirmations from members who are members *at execution time*, not from a historical, possibly-revoked set. Since `MultiSigRequestAction::Transfer` and `FunctionCall` can move NEAR or call arbitrary contracts on the multisig's behalf, a stale confirmation can let a request (e.g. a `Transfer`) execute below the intended live threshold — a Critical impact per the rules ("a multisig request executed below threshold").

### Likelihood Explanation
Requires a normal, expected workflow: a member confirms a pending request, is later removed (e.g., for key rotation, compromise, or offboarding — a legitimate and expected multisig operation, not a privileged attacker action from the perspective of the remaining members), and the same request is still open when new confirmations are added. Given the default `active_requests_limit` of 12 and no forced expiry tied to membership changes, this is a plausible, easily triggered ordering of ordinary operations rather than a contrived edge case.

### Recommendation
When removing a member in `delete_member`, iterate all entries in `self.confirmations` (or maintain a reverse index) and remove the deleted member's identity from every confirmation set, not just from requests it created. Alternatively, when counting confirmations in `confirm()`, filter `confirmations` against `self.members` before comparing to `num_confirmations`, so removed members' stale confirmations never count.

### Proof of Concept
1. Initialize `multisig2` with members `A, B, C` and `num_confirmations = 2`.
2. `A` calls `add_request` to create request `R1` (e.g., `Transfer`).
3. `B` calls `confirm(R1)` → `confirmations[R1] = {B}` (size 1 < 2, not yet executed).
4. Separately, `A` and the required threshold create/execute a `DeleteMember { member: B }` request, removing `B` from `self.members`. `delete_member` only touches requests created by `B`; `confirmations[R1]` still contains `B`.
5. `C` calls `confirm(R1)` → `confirmations.len() + 1 == 2 >= num_confirmations`, so `R1` executes, counting `B`'s stale confirmation as valid even though `B` is no longer a member — the transfer executes with only one live confirmation (`C`) instead of the required two.

### Citations

**File:** multisig2/src/lib.rs (L304-304)
```rust
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
```

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
