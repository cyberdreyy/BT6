Found a solid analog. In `multisig2/src/lib.rs`, `delete_member` only purges confirmations for requests **created** by the removed member, not confirmations **cast by** that member on other pending requests. Those stale confirmations remain in the `confirmations` set and are still counted by `confirm()` against the live `num_confirmations` threshold, letting a request execute with fewer than `num_confirmations` confirmations from currently-live members. The same pattern exists in the older `multisig/src/lib.rs` via `DeleteKey`.Confirmed the same pattern exists in the original `multisig/src/lib.rs` as well: `confirm` counts raw entries in the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set without re-validating that each key is still a currently-authorized signer, and `DeleteKey` only purges confirmation records for requests where `r.signer_pk == pk` (i.e., requests *created* by that key), not confirmations *cast* by that key on other pending requests [1](#0-0) .

### Title
Stale confirmations from removed/deleted multisig members are still counted toward `num_confirmations`, allowing a request to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether a request has enough approvals purely by counting entries already stored in the `confirmations` set/map for that `request_id`, compared against the current `num_confirmations` [2](#0-1) . When a member is removed via `DeleteMember` (multisig2) or a key is removed via `DeleteKey` (multisig), the cleanup logic only removes requests and confirmations for requests that the removed member/key itself *created* (`r.member == member` / `r.signer_pk == pk`), never confirmations that the removed member had previously *cast* on other pending requests created by someone else [3](#0-2) . Those stale confirmations remain in the `confirmations` set and continue to count toward the threshold check in `confirm`, so a request can later be executed with fewer live-member approvals than `num_confirmations` actually requires.

### Finding Description
The binding that should hold is: `confirmations counted in a request's confirmation set == confirmations from members that are still live at the time the threshold is evaluated`. This binding is broken because member removal is request-creator-scoped cleanup, not confirmer-scoped cleanup.

- `delete_member` (multisig2) removes the member from `self.members`, deletes their access key on-chain, and purges only `requests`/`confirmations` for requests where `r.member == member` (i.e., requests *added* by that member) [4](#0-3) .
- It never scans `confirmations` entries for other, still-pending requests to strip out this member's prior approval.
- `confirm` later does `confirmations.len() as u32 + 1 >= self.num_confirmations` to decide whether to execute the request, treating every string already in the set as a valid, current approval [5](#0-4) .
- The exact same gap exists in the older `multisig/src/lib.rs`: `DeleteKey` filters `requests` by `r.signer_pk == pk` and clears confirmations only for those (i.e., requests created by the deleted key), leaving any confirmation this key gave on other requests untouched.

Because a request's confirmation count is never re-validated against the live member/key set at confirm-time (only the *confirming* caller is checked via `assert_valid_request`/`current_member`), a confirmation cast by a member before their removal is permanently "frozen in" as a valid vote, even after that member has been fully deleted from the multisig.

### Impact Explanation
This directly matches the "multisig request executed below threshold" critical impact category. A K-of-N multisig's entire security model rests on requiring K confirmations from the N *currently* live/trusted signers. With this bug, a request (e.g., a `Transfer` moving NEAR out of the multisig account, or a self-request adding a malicious `AddMember`/`AddKey`) can execute after receiving effectively fewer than K confirmations from live members, because one or more of the counted confirmations belong to a member who has since been removed and stripped of on-chain signing ability. This is an unauthorized move of funds/authority relative to the party's actual, current trust configuration.

### Likelihood Explanation
No special privileges are required beyond being a normal multisig member at the time of confirming (which is the expected threat model for this contract). The sequence — request created, partially confirmed by a member, that member later removed by a separate `DeleteMember`/`DeleteKey` request, then a remaining member supplies the final confirmation — is a plausible, easily reachable governance sequence, especially in scenarios of key rotation, offboarding, or compromised-key remediation where the multisig continues finishing already in-flight requests.

### Recommendation
When removing a member/key (`delete_member` in `multisig2`, `DeleteKey` in `multisig`), iterate over **all** active `requests`/`confirmations`, not just those created by the removed member, and strip that member's string/public-key from every confirmation set. Alternatively, re-validate at `confirm` time that every entry in the `confirmations` set still corresponds to a current member of `self.members` (multisig2) before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R1` (e.g., `Transfer` of the multisig's NEAR balance to an attacker-controlled account).
3. `B` calls `confirm(R1)` → `confirmations = {B}` (1 < 3, not executed).
4. `C` calls `confirm(R1)` → `confirmations = {B, C}` (2 < 3, not executed).
5. Separately, a `DeleteMember { member: B }` request is created and confirmed by 3 members, executing and removing `B` from `self.members` and deleting `B`'s access key. `delete_member` only removes requests/confirmations where `r.member == B` (requests *created* by `B`); `R1` was created by `A`, so `R1`'s confirmation set `{B, C}` is untouched [4](#0-3) .
6. `D` calls `confirm(R1)`: `confirmations.len()` is `2`, `2 + 1 >= 3` → `R1` executes, transferring funds out of the multisig even though only `C` and `D` are still live members who actually approved it — one fewer live-member confirmation than the configured `num_confirmations` threshold requires.

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
