### Title
Multisig `confirm` counts stale confirmations from removed members, allowing request execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations stored as plain strings in a `HashSet<String>` per request, but never re-validates that each stored confirmer is still a current member of `self.members` at the time the threshold check is performed. `delete_member` only purges *requests originated by* the removed member, not *confirmations that member previously cast on other members' pending requests*. A confirmation from an account that has since been removed from the multisig therefore still counts toward `num_confirmations`, letting a request execute with fewer live, currently-authorized signers than the configured threshold.

### Finding Description
`confirm` reads the confirmation set for a request and, once `confirmations.len() + 1 >= self.num_confirmations`, executes the request: [1](#0-0) 

Membership removal is handled by `delete_member`, which only cleans up requests where `r.member == member` (i.e. requests the removed member *created*) and the removed member's own `num_requests_pk` entry. It never scans `self.confirmations` to strip that member's confirmations from requests created by *other* members: [2](#0-1) 

Because `confirmations` is a bare `HashSet<String>` keyed by the stringified `MultisigMember`, and `confirm`/`current_member` never cross-check the stored strings against the live `self.members` set when tallying the count, a confirmation cast before removal remains permanently valid evidence toward the threshold even after the member is deleted: [3](#0-2) [4](#0-3) 

This is structurally the same class of bug as the reported `Clearinghouse.claimDefaulted` issue: a privileged aggregate value (`totalCollateral` recovered / confirmation count reaching threshold) is computed by trusting individual inputs (loans / confirmations) without verifying they originate from, or still belong to, the trusted authority (the Clearinghouse / the current member set). Here, the "recorded confirmations" diverge from "live members," which is one of the custody bindings explicitly in scope.

### Impact Explanation
This allows a `MultiSigRequest` (including `Transfer`, `FunctionCall`, `AddKey`, etc.) to be executed with fewer *currently valid* confirmations than `num_confirmations` requires, because a phantom confirmation from a removed member is still tallied. This is a multisig request executed below threshold — a Critical impact per the rules, since it breaks the fundamental K-of-N security guarantee of the wallet and can result in unauthorized transfers or key additions.

### Likelihood Explanation
The scenario requires only ordinary multisig operation, not any protocol-breaking action: a member confirms a pending request created by another member, and is subsequently removed via a legitimate `DeleteMember` action (e.g. because they were suspected of being compromised or colluding on that very request). Because member rotation/removal is a normal, expected multisig lifecycle event, and `delete_member` does not sweep confirmations on requests it didn't create, this stale-confirmation condition can arise without any privileged bypass — it is a latent flaw in the accounting invariant "confirmations counted == live members."

### Recommendation
When tallying confirmations in `confirm` (and when exposing `get_confirmations`/`get_num_confirmations`), filter the stored confirmation set to only those entries still present in `self.members` before comparing against `num_confirmations`. Additionally, have `delete_member` remove the deleted member's identifier from all entries in `self.confirmations`, not just from requests they authored, so that no stale confirmation can ever be counted after a member is removed.

### Proof of Concept
1. Deploy a multisig (`multisig2`) with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `B` calls `add_request` with a `Transfer` action sending funds to an address controlled by `A`.
3. `A` calls `confirm(request_id)` → confirmations = `{A}` (count 1 < 3, stored via `confirmations.insert(member.to_string())` at [5](#0-4) ).
4. `B`, `C`, `D` separately raise and confirm a `DeleteMember { member: A }` request against `A` (3 confirmations reach the threshold, executing `delete_member`); this only removes requests where `r.member == A` (line 365) — the transfer request from step 2 (authored by `B`) is untouched, and `A`'s confirmation on it is never purged.
5. Members are now `{B, C, D}`; `num_confirmations` is unchanged at 3.
6. `B` calls `confirm(request_id)` on the transfer request → confirmations = `{A, B}` (count 2 < 3).
7. `C` calls `confirm(request_id)` → `confirmations.len() as u32 + 1 = 3 >= self.num_confirmations` triggers `execute_request`, transferring funds despite only `B` and `C` being currently valid members who approved it — one fewer live confirmation than the configured threshold, with `A`'s stale, post-removal confirmation making up the difference.

### Citations

**File:** multisig2/src/lib.rs (L126-133)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
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
