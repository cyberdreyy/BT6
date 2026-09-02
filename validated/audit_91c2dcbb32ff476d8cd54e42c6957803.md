### Title
Multisig `DeleteMember` fails to purge confirmations on requests it did not create, allowing execution below the configured threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only removes pending requests that were **created** by the member being removed; it never scans other members' confirmation sets. A confirmation cast by a member on someone else's request survives that member's removal, so it is still counted toward `num_confirmations` even though the multisig no longer trusts that identity. This lets a request execute with fewer live-member confirmations than the configured K-of-N threshold.

### Finding Description
`confirm()` decides whether to execute a request purely by comparing the size of the stored `confirmations` `HashSet<String>` to `self.num_confirmations`: [1](#0-0) 

Confirmations are keyed by `member.to_string()` (account id or public key), and stored per `request_id` independently of the live `members` set: [2](#0-1) 

When a member is removed via `DeleteMember`, `delete_member` cleans up only the requests where `r.member == member` — i.e., requests *originated* by that member — and their confirmations. It does not walk `self.confirmations` for other requests to strip entries contributed by the removed member: [3](#0-2) 

The intended invariant is: *the number of confirmations counted for a request should equal the number of currently-live members who confirmed it* (`counted confirmations == live confirming members`). Because `delete_member` doesn't purge stale entries from requests it didn't create, this equality breaks: a removed member's stale confirmation remains in the set and is still summed against `num_confirmations` in `confirm()`.

### Impact Explanation
This falls under the Critical category "a multisig request executed below threshold." A request (e.g., a `Transfer` action moving NEAR out of the multisig account, or an `AddKey`/`AddMember` action granting control) can be executed with confirmations from only `K-1` (or fewer) currently-live members, plus one stale confirmation from an already-removed member, while still satisfying `confirmations.len() + 1 >= num_confirmations`. This directly undermines the K-of-N authorization guarantee the contract is supposed to enforce and can result in unauthorized NEAR transfers or unauthorized key/member changes.

### Likelihood Explanation
This requires no special privilege beyond being (or having been) a legitimate multisig member/key holder at some point, plus normal governance flow (member removal via `DeleteMember`, which is an expected, routine operation for any multisig — e.g., rotating out a departing team member or a compromised key). Any request that (a) is created by member X, (b) is confirmed by member Y (not X) before reaching threshold, and (c) has Y later removed from the multisig for any legitimate reason, will retain Y's stale confirmation indefinitely (there is no expiry other than the 15-minute `delete_request` cooldown, and `delete_request` must be explicitly and separately invoked). This is a realistic, easily triggered sequence rather than a contrived edge case.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests created by the removed member) and remove the removed member's key from every confirmation set, or lazily filter confirmations against the current live `members` set at count-time inside `confirm()` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy multisig with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. Member `B` calls `add_request` creating request `R` — e.g., `Transfer { amount }` to an attacker-controlled/receiver account (`r.member == B`).
3. Member `A` calls `confirm(R)`. `confirmations[R] = {A}` (1/3).
4. Separately, members `B`, `C`, `D` jointly pass an unrelated `DeleteMember { member: A }` request (3 confirmations, satisfying the current threshold) — perhaps because `A`'s key is suspected compromised. `delete_member` only deletes requests where `r.member == A`; since `R` was created by `B`, `R` and its confirmation set `{A}` are left untouched. Live members are now `{B, C, D}`.
5. Member `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2/3, and `A` is no longer live).
6. Member `C` calls `confirm(R)` → `confirmations.len() + 1 = 3 >= num_confirmations(3)`, so `execute_request` fires the `Transfer`, even though only 2 currently-live members (`B`, `C`) ever confirmed — the threshold of 3 live confirmations was never actually met.

### Citations

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
