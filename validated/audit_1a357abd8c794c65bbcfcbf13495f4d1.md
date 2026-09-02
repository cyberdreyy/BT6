Confirmed. `delete_member` at multisig2/src/lib.rs:356-379 only purges the confirmation sets of requests whose *originating* member (`r.member`) equals the member being removed; it never scans the `confirmations: LookupMap<RequestId, HashSet<String>>` map itself to strip that member's confirmation string from requests they merely *confirmed* (but didn't create). `confirm` at multisig2/src/lib.rs:294-315 then counts `confirmations.len() as u32 + 1 >= self.num_confirmations` treating every entry in the stale set as a live authorizer, so a removed member's stale confirmation still counts toward the threshold.

### Title
Removed multisig member's stale confirmation still counts toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` fails to purge a deleted member's confirmation from requests that member confirmed but did not originate, allowing a request to execute with fewer live, authorized confirmations than `num_confirmations` requires.

### Finding Description
Confirmations are tracked per request in `confirmations: LookupMap<RequestId, HashSet<String>>` [1](#0-0)  and are keyed by `member.to_string()`, not by request-originator. When a member is removed via `DeleteMember`, `delete_member` only removes confirmation sets for requests whose *originating* `member` field equals the removed member: `self.requests.iter().filter_map(|(k, r)| if r.member == member { Some(k) } else { None })` followed by `self.confirmations.remove(&request_id)` [2](#0-1) . It never iterates the full `confirmations` map to strip the removed member's string from sets of *other* requests they had confirmed as a non-originating signer.

`confirm` treats the raw size of that stale set as the number of live confirmations: `if confirmations.len() as u32 + 1 >= self.num_confirmations { ... execute_request(request) }` [3](#0-2) . Because the member's account/key was already removed from `self.members` by `delete_member` (`self.members.remove(&member)` [4](#0-3) ), a request originally created by a different, still-active member can be pushed over `num_confirmations` by combining one live confirmation with the ghost confirmation left behind by the removed member — breaking the invariant that `confirmations counted == live members who authorized`.

### Impact Explanation
This crosses the threshold-authorization boundary the multisig is designed to enforce: a request (including `Transfer`, `DeployContract`, `AddKey`/`AddMember` self-requests) can execute with fewer currently-authorized signers than `num_confirmations`, effectively lowering the K-of-N requirement after a member removal. Per the rules this is Critical — "a multisig request executed below threshold."

### Likelihood Explanation
This requires no privileged action from the attacker beyond being (or having been) a legitimate multisig member at some point and later removed — a routine operational event (member rotation, key compromise mitigation, offboarding). Any request that the removed member confirmed prior to removal but that wasn't executed/deleted remains in `requests`/`confirmations` with the stale entry, ready to be combined with fewer live confirmers than intended.

### Recommendation
When deleting a member, iterate all outstanding requests (not just those it originated) and remove the member's string from every confirmation `HashSet`, or re-validate on `confirm`/execution that every entry in a request's confirmation set still corresponds to a current member of `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members=[A, B, C], num_confirmations=3)`.
2. Member A calls `add_request` creating request R (e.g., a `Transfer`).
3. Member B calls `confirm(R)` → `confirmations[R] = {B}` (len 1, below threshold, stored).
4. Members execute a separate self-request `DeleteMember { member: B }` with 3 confirmations, which succeeds because at this point all three members are still active; `delete_member` runs `filter_map` over `requests` — R's `r.member == A != B`, so R and its confirmations set `{B}` are **not** removed; only `self.members` no longer contains B.
5. Member C now calls `confirm(R)`: `confirmations.len() (1, containing removed B) + 1 (C) = 2` — still short of 3 in this example, but with `num_confirmations = 2` and members `[A,B,C]`→ after removing B (still ≥ num_confirmations=2 check passes), C's confirm alone satisfies `1 (stale B) + 1 (C) >= 2`, executing R despite only A (creator, uncounted in the raw check) and C ever being live confirmers at execution time — i.e., a request meant to need 2 *current* member confirmations executes on 1 live confirmation (C) plus a ghost (B). [5](#0-4) [6](#0-5)

### Citations

**File:** multisig2/src/lib.rs (L126-128)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
```

**File:** multisig2/src/lib.rs (L294-315)
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
