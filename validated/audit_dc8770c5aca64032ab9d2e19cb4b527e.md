### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
The multisig contract's `confirm` function tallies confirmations purely by counting entries already stored in the `confirmations` map for a request, without re-validating that each recorded confirmer is still a current member of `self.members`. This mirrors the `floatingBackupBorrowed` bug class: a value that feeds into a threshold/utilization decision (`confirmations.len()`) can become stale relative to the state it is supposed to reflect (live membership), and nothing forces a "settlement" (re-check or purge) before it is used to authorize execution.

### Finding Description
`confirm()` reads the confirmation set for a request and executes it once the count reaches `num_confirmations`: [1](#0-0) 

The check is `confirmations.len() as u32 + 1 >= self.num_confirmations`, a pure cardinality check against a `HashSet<String>` of member identifiers that were recorded whenever `confirm` was previously called. There is no re-validation step that intersects the stored confirmations with the currently-live `members: UnorderedSet<MultisigMember>`: [2](#0-1) 

Only the *new* signer calling `confirm` is checked for current membership via `current_member()`. Previously recorded confirmations from members who have since been removed (e.g., via a `DeleteMember` action executed on an unrelated request) remain in the `confirmations` map for every *other* pending request untouched, because `remove_request` only clears confirmations for the request it is executing/deleting, not for all other outstanding requests: [3](#0-2) 

The binding that should hold is: `confirmations counted for execution == confirmations from members that are live at execution time`. Because stale confirmations are never purged or re-validated when membership changes, this equality can be broken — a request can execute with `num_confirmations` votes even though fewer than `num_confirmations` of those voters are still members at the time of execution.

### Impact Explanation
This falls under the Critical impact category "a multisig request executed below threshold." If, say, `num_confirmations = 3`, and member A confirms request X, then a separate request removing A from the multisig is executed, A's earlier confirmation on X still counts. Only 2 *live* members then need to confirm X for it to execute, effectively lowering the real security threshold to `num_confirmations - 1` for any request that had confirmations collected before a membership change. This can lead to unauthorized transfers, key additions, or contract deployments being pushed through with fewer genuinely-live approvals than the configured threshold.

### Likelihood Explanation
Exploitation does not require any special privilege beyond being (or having been) a legitimate multisig member — it only requires timing: get a confirmation recorded, then have (or wait for) that member to be removed via a separate, otherwise-legitimate `DeleteMember` request, and then push the original request to execution with fewer live confirmers than `num_confirmations`. This is a realistic operational sequence in any multisig that rotates membership (which the contract explicitly supports via `AddMember`/`DeleteMember`), not a contrived edge case.

### Recommendation
When executing a request in `confirm()` (or whenever membership changes via `AddMember`/`DeleteMember`), re-validate that every account/key in the request's `confirmations` set is still present in `self.members` before counting it toward `num_confirmations`, or proactively purge/invalidate confirmations tied to a member as soon as that member is removed.

### Proof of Concept
1. Initialize `MultiSigContract::new` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm` for request `X` (transfer of funds) — confirmations for `X` = `{A}`.
3. Separately, members confirm and execute a `DeleteMember { member: A }` request, removing `A` from `self.members`. `X`'s confirmations map is untouched (only the `DeleteMember` request's own confirmations were cleared via `remove_request`), per [3](#0-2) .
4. `B` and `C` (both still live members) call `confirm(X)`. The check `confirmations.len() + 1 >= num_confirmations` (i.e., `2 + 1 >= 3`) succeeds and `X` executes — even though only `B` and `C` are actually live members who approved it, one fewer than the configured `num_confirmations = 3`.

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
