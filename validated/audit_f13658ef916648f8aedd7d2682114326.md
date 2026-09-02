## Title
Multisig `delete_member` fails to purge a removed member's confirmations from requests they did not create, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm()` authorizes execution purely by counting entries in the `confirmations` `HashSet<String>` for a request: `if confirmations.len() as u32 + 1 >= self.num_confirmations`. `delete_member()` only removes *requests* whose creator (`r.member`) is the member being deleted; it never scrubs that member's `confirmations` entries from other requests they merely *confirmed*. This breaks the intended custody binding "confirmations counted == confirmations from currently live members", allowing a request to execute with fewer than `num_confirmations` live, authorized approvals.

### Finding Description
`confirm()` trusts the raw size of the `confirmations` set as a proxy for "number of members who approved this request," without re-validating that each stored confirming identity is still a current member: [1](#0-0) 

`delete_member()` only cleans up requests where the removed member is the *creator* (`r.member == member`); it does not scan `self.confirmations` for entries where the removed member appears only as a *confirmer* on someone else's request: [2](#0-1) 

The `confirmations` map stores raw serialized member identifiers as strings, independent of the live `members: UnorderedSet<MultisigMember>` set: [3](#0-2) 

Attack path:
1. Member `A` creates request `R` (e.g. `Transfer` of contract funds) via `add_request`.
2. Member `B` (a distinct member) calls `confirm(R)`, which inserts `B`'s identity into `confirmations[R]` — not yet reaching threshold.
3. Through a separate, properly-confirmed `DeleteMember` request, `B` is later removed from the multisig (e.g. because `B` left the organization or their key was rotated/compromised). `delete_member` only clears requests *created* by `B`; `R` (created by `A`) is untouched, so `confirmations[R]` still contains `B`'s stale entry.
4. Some later live member `C` calls `confirm(R)`. The check `confirmations.len() + 1 >= num_confirmations` now counts the ghost approval from removed member `B` together with the live approvals from `A` (implicitly, since add_request_and_confirm patterns can include the creator) and `C`, letting `R` execute with fewer genuinely live, current confirmations than `num_confirmations` requires.

This directly breaks the equality "confirmations counted == confirmations from currently authorized live members," which is the exact custody binding called out for multisig contracts (confirmations counted versus live members).

### Impact Explanation
This allows a multisig request (including `Transfer`, `AddKey`/`AddMember`, `DeployContract`, etc.) to execute with confirmations below the configured `num_confirmations` threshold once membership has changed after a pending request was partially confirmed. This maps to the Critical impact category: "a multisig request executed below threshold," directly threatening custody of NEAR held by the multisig account.

### Likelihood Explanation
No attacker privilege beyond normal multisig operation is required — it only needs the ordinary combination of (a) a pending, partially-confirmed request, and (b) a subsequent legitimate member removal (a routine governance action, e.g., key rotation or offboarding), which is common in the natural lifecycle of a multisig. No malicious insider, redeploy, or external interception is needed; it's a latent bookkeeping bug reachable through completely standard usage patterns.

### Recommendation
When deleting a member in `delete_member`, iterate over `self.confirmations` for all outstanding requests and remove the deleted member's identifier from every confirmation set, not only from requests the member created. Alternatively, revalidate at `confirm()` time that every entry in `confirmations` still corresponds to a current member of `self.members` before counting it toward the threshold.

### Proof of Concept
1. `new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A.add_request(transfer_request)` → `request_id = R`, `confirmations[R] = {}`.
3. `B.confirm(R)` → `confirmations[R] = {B}` (len 1 < 3, no execution).
4. Members execute a separate confirmed `DeleteMember { member: B }` request → `delete_member` removes `B` from `self.members`, but `confirmations[R]` is untouched because `R.member == A`, not `B`.
5. `C.confirm(R)` → `confirmations[R] = {B, C}` (len 2 < 3, no execution yet).
6. `A.confirm(R)` → `confirmations.len() as u32 + 1 == 3 >= num_confirmations (3)` → `execute_request` runs the transfer, even though the currently live confirming members are only `{A, C}` (2 live approvals), one short of the required 3, because the removed member `B`'s stale confirmation was still counted. [4](#0-3) [5](#0-4)

### Citations

**File:** multisig2/src/lib.rs (L120-133)
```rust
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
