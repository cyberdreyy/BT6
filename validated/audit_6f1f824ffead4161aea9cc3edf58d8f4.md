## Analog Found

### Title
Stale confirmations from removed multisig members still count toward quorum, allowing request execution below the live threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests that the *removed member itself created* (`r.member == member`) and clears that member's `num_requests_pk` counter. It never scans the `confirmations` map to strip that member's confirmation entries from *other* pending requests they merely voted on. `confirm()` and `assert_valid_request()` only re-validate the identity of the *current* caller against `self.members`; they never re-validate the members whose confirmations are already stored in a request's `HashSet<String>`. As a result, a confirmation cast by a member before their removal keeps counting toward `num_confirmations` for a pending request created by someone else, letting the remaining (fewer) live members push that request past quorum using a "ghost" vote from a no-longer-authorized member.

### Finding Description
The threshold check in `confirm()` is: [1](#0-0) 

and membership validity is checked only for the immediate caller in `assert_valid_request` / `current_member`: [2](#0-1) 

Member removal is handled here: [3](#0-2) 

Note that the `request_ids` filter only matches requests whose `member` field (the *creator*) equals the removed member; it does not touch `self.confirmations` entries belonging to that member on requests created by someone else. Since `MultiSigRequestWithSigner.member` only records the creator, and `confirmations: LookupMap<RequestId, HashSet<String>>` is populated in `confirm()` by inserting `member.to_string()`: [4](#0-3) 

any confirmation a member cast before being removed remains stored and is still counted in `confirmations.len() as u32 + 1 >= self.num_confirmations` on subsequent calls to `confirm()`, even though that member no longer exists in `self.members`.

This breaks the equality that should hold: **live confirmations counted == live members who actually confirmed**. Post-removal, the contract effectively treats a removed member's stale vote as still valid, letting the remaining members reach the configured `num_confirmations` threshold with strictly fewer currently-authorized signers than intended.

### Impact Explanation
This is Critical: a multisig request (e.g. `Transfer`, `FunctionCall`, or `AddKey` to a controlled account) can be executed below the configured threshold. Funds custodied by the multisig, or privileged calls gated behind "N-of-M live members," can be moved/executed with fewer than N genuinely live authorizations, directly matching "a multisig request executed below threshold."

### Likelihood Explanation
No foundation, redeploy, victim key, or social engineering is required — only ordinary use of the contract's own public methods (`add_request`, `confirm`, and a `DeleteMember` request) by members who are already part of the multisig at the time they act. Membership churn (removing a departing/compromised member) is an expected, routine multisig operation, making the stale-confirmation window realistic to hit in normal operation, not just a contrived attack.

### Recommendation
When executing `DeleteMember`, iterate over all pending `requests`/`confirmations` (not only those created by the removed member) and strip the removed member's entry from every `confirmations` `HashSet`. Alternatively, re-validate at execution time in `confirm()`/`execute_request()` that every account/key present in the stored confirmation set is still a current member, discarding stale entries (and decrementing the effective count) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request` creating request `R` (e.g. `Transfer { amount }` to an attacker-controlled account). `R.member == A`.
3. `B` calls `confirm(R)`. `confirmations[R] = {B}` (len 1 + 1 = 2 < 3, not yet executed).
4. Members vote out `B` via a separate `DeleteMember { member: B }` request, gathering confirmations from `A`, `C`, `D` (3 confirmations, satisfies quorum). `delete_member` executes: since no request has `r.member == B`, request `R`'s `confirmations[R] = {B}` is left untouched; `self.members` becomes `{A, C, D}` (still `>= num_confirmations`, passes the internal assert).
5. `A` calls `confirm(R)`: `confirmations[R].len()==1 (+1 for A) == 2 < 3`, so it stores `confirmations[R] = {A, B}`.
6. `C` calls `confirm(R)`: `confirmations[R].len()==2, +1 = 3 >= 3` → `execute_request` fires, transferring funds.
7. Final tally: quorum was reached using `{A, B, C}`, but `B` was removed in step 4 and is no longer a member. Only `A` and `C` are truly live confirmers — one fewer live confirmation than the configured `num_confirmations = 3` — yet the transfer executed.

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
