### Title
Stale confirmations from removed multisig members allow request execution below the live confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts confirmations stored in a `HashSet` keyed by request ID and executes the request once the count reaches `num_confirmations`, without re-validating that each confirming identity is still a current member. `delete_member` only purges pending requests that were *originally submitted* by the removed member; it does not scrub that member's confirmations from other pending requests they had already confirmed. This lets a request accumulate confirmations from members who are later removed from the multisig, and still execute once the numeric threshold is reached, effectively executing with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` performs the threshold check purely on set cardinality: [1](#0-0) 

The only membership check performed is on the *current caller* via `assert_valid_request` → `current_member()`; nothing re-verifies that previously recorded confirmations still belong to current members.

`delete_member` (multisig2) removes the member from `self.members` and deletes any *requests they authored*, but for requests authored by someone else that this member had already confirmed, the confirmation entry is left untouched in `self.confirmations`: [2](#0-1) 

Sequence that breaks the binding `confirmations counted == live members who approved`:
1. Multisig has N members, `num_confirmations = K`. A member submits a sensitive request (e.g., `Transfer`, `AddKey`, `DeployContract`).
2. Member A confirms it (count = 1, less than K).
3. Member A is later removed via a separate `DeleteMember` request that reaches quorum for unrelated/legitimate reasons. `delete_member` does not touch the earlier request's confirmation set, so A's confirmation is still counted.
4. Enough *current* members confirm the still-pending request until `confirmations.len() + 1 >= K` is satisfied — but one of the counted confirmations (A's) came from an account that is no longer a member at execution time.
5. `execute_request` runs the action (transfer of funds, deploying new contract code, adding an access key, etc.) even though the number of *live* member approvals was `K - 1`, one below the configured threshold.

### Impact Explanation
This breaks the custody/authorization binding explicitly called out as Critical: "a multisig request executed below threshold." An attacker or accomplice who can get temporarily added as a member, confirm a high-value request, then get removed (or is removed for unrelated governance reasons), still leaves their confirmation "baked in," letting the request execute with insufficient live approvals. Because `MultiSigRequestAction` includes `Transfer`, `AddKey` (full access key), and `DeployContract`, executing with a diluted/stale confirmation set can lead to unauthorized fund transfers or contract takeover — a direct violation of the K-of-N custody guarantee the contract is supposed to enforce.

### Likelihood Explanation
This requires membership churn (an `AddMember`/`DeleteMember` action) while a request is pending — a plausible, non-privileged-attacker-independent event in normal multisig lifecycle management (e.g., rotating a compromised key, offboarding a member). No foundation, victim key, or redeploy is needed; any member set change combined with a pending, partially-confirmed request triggers the mismatch. Because `delete_member` is a normal multisig action (not a rare edge case), the window for this to occur in production key-rotation workflows is realistic.

### Recommendation
When `delete_member` (or `DeleteKey`/`AddKey` account removal in `multisig/src/lib.rs`) removes a member, iterate all pending requests' confirmation sets and strip that member's confirmation entry (not just the requests they authored). Alternatively, revalidate at `confirm()`/execution time that every entry in the stored confirmations set is still `self.members.contains(...)` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members=[A,B,C,D], num_confirmations=3)`.
2. `A` submits `add_request` for `Transfer{amount: X}` to an external attacker-controlled receiver.
3. `A` calls `confirm` (confirmations = {A}, count 1).
4. Separately, `B`, `C`, `D` confirm and execute a `DeleteMember{member: A}` request (A removed from `self.members`); per `delete_member`, only requests where `r.member == A` (i.e., authored by A) are purged — the pending `Transfer` request A merely confirmed is untouched, and A's entry remains in its `confirmations` set.
5. `B` and `C` now call `confirm` on the pending `Transfer` request: confirmations.len() (1, from A) + 1 (B) = 2 < 3 → stored; then C confirms: 2 + 1 = 3 >= 3 → `execute_request` runs the transfer.
6. Result: the transfer executed with only 2 live-member confirmations (B, C) even though `num_confirmations = 3`, because A's stale confirmation (from a now-removed member) was counted toward the threshold. [3](#0-2)

### Citations

**File:** multisig/src/lib.rs (L248-266)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L118-133)
```rust
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
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
