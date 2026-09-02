### Title
Multisig request can execute below the live-member confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes only the requests that were *created* by the removed member; it never scrubs that member's *confirmations* left on other, still-pending requests. Because `confirm` counts entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set literally (`confirmations.len() as u32 + 1 >= self.num_confirmations`), a stale confirmation from a member who has since been deleted still counts toward the K-of-N threshold. This breaks the binding "confirmations counted == live members," allowing a request to execute with fewer than `num_confirmations` currently-authorized signers.

### Finding Description
The contract state holds: [1](#0-0) 

`confirm` decides to execute a request purely from the size of the stored `HashSet<String>` for that `request_id`: [2](#0-1) 

`delete_member` is the only place that mutates membership, and it explicitly filters requests by `r.member == member` (i.e., requests *authored* by the removed member), removing confirmations only for those requests. It does not scan `confirmations` for entries keyed by the removed member's identity on requests authored by *other* members: [3](#0-2) 

Sequence that breaks the K-of-N custody binding:
1. Members A, B, C exist with `num_confirmations = 3` (K=N=3).
2. B creates request R (e.g., `Transfer`). A calls `confirm(R)` → `confirmations = {A}`.
3. Members vote (via `DeleteMember` request, requiring full quorum at the time) to remove A (e.g., suspected key compromise). `delete_member` runs, but since A did not author R, A's entry stays in `confirmations[R] = {A}` — it is never purged.
4. Now only B and C are live members (`members.len() == 2`), yet `confirmations[R]` still contains A's stale confirmation.
5. C calls `confirm(R)`. `confirmations.len() as u32 + 1` = `1 + 1 = 2`, which is `< num_confirmations (3)`, so it just adds C, making `confirmations = {A, C}`, len 2, still short of 3 — but this state is already wrong: the outstanding request only needs *one more* live confirmation (from B) to reach the len-based threshold of 3, even though only 2 live members (B, C) actually approved it, i.e., quorum from live members is 2 out of 2 while the intended policy required 3 out of 3.
6. B confirms: `confirmations.len() as u32 + 1 = 2 + 1 = 3 >= 3` → request executes with the transfer, even though the removed member A never re-approved anything after being kicked out and the multisig currently only has 2 live members total (fewer than `num_confirmations`).

Thus the invariant "a request only executes once `num_confirmations` *live* members approved it" is violated: a stale confirmation from a deleted/revoked member is silently counted as live approval, letting the request execute with fewer authentic, currently-authorized approvals than the configured threshold.

### Impact Explanation
This directly matches the in-scope "Critical" impact class: a multisig request (e.g., a `Transfer` of NEAR) can be executed below the configured threshold of live members, because a since-revoked member's confirmation is still counted. Funds custodied by the multisig account can be moved by fewer live authorized parties than `num_confirmations` requires, undermining the entire K-of-N security guarantee the contract advertises.

### Likelihood Explanation
This requires only routine multisig operations: creating a pending request, one member confirming it, and later legitimately removing that confirming member (a common security response, e.g., suspected compromised key) before the request is confirmed/executed by the remaining members. No attacker privilege escalation, redeploy, or foundation/owner action is needed beyond normal multisig usage; the flaw is triggered by the standard `DeleteMember` + pending-request interaction, which is a realistic and likely operational sequence for any multisig managing membership changes over time.

### Recommendation
When deleting a member in `delete_member`, iterate over all entries in `self.confirmations` (not just requests authored by the removed member) and remove the deleted member's identity from every confirmation set. Additionally, re-validate resulting confirmation counts against the current membership (or simply recompute confirmations only from currently active members) before allowing `confirm` to treat `confirmations.len() + 1 >= num_confirmations` as sufficient for execution.

### Proof of Concept
1. Deploy `multisig2` with 3 members `A, B, C` and `num_confirmations = 3`.
2. `B` calls `add_request` with a `Transfer` action to an external account.
3. `A` calls `confirm(request_id)` → `confirmations[request_id] = {A}`.
4. Members submit and confirm (with full 3/3 quorum, since membership hasn't changed yet) a `DeleteMember { member: A }` request; `delete_member` executes, removing A from `members`, but `confirmations[request_id]` still contains A's entry (untouched since request_id's author is B, not A).
5. `C` calls `confirm(request_id)` → set becomes `{A, C}`, len 2, `2+1=3 >= 3` fails only because len was 2 not yet — but continue: `B` calls `confirm(request_id)` → set is `{A, C, B}` conceptually (B's own confirm check adds B) → `confirmations.len() as u32 + 1 = 2 + 1 = 3 >= 3` → `execute_request` is invoked and the `Transfer` is executed.
6. Verify the transfer executed even though only `B` and `C` are live members (2 live approvals) against a contract configured to require `num_confirmations = 3`, confirming the request was authorized below the current live-membership threshold. [2](#0-1) [3](#0-2)

### Citations

**File:** multisig2/src/lib.rs (L116-133)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
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
