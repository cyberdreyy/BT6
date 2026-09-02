### Title
Confirmations from a deleted multisig member remain counted toward the confirmation threshold, allowing a request to execute with fewer live signers than `num_confirmations` requires - (File: `multisig2/src/lib.rs`)

### Summary
The external report describes an Ethos Network bug where a mapping that marks an address as "compromised" is never cleared when the address is restored, leaving stale state that misrepresents the address's real status. The same bug class — a boolean/set membership record that is not purged when the underlying entity's status changes — exists in `multisig2/src/lib.rs`: when a multisig member is deleted, their prior *confirmations* on requests created by other members are never scrubbed from `self.confirmations`, so a deleted (no-longer-a-member) signer's stale confirmation keeps counting toward `num_confirmations`.

### Finding Description
`MultiSigContract::confirm()` counts entries in `self.confirmations.get(&request_id)` (a `HashSet<String>`) against `self.num_confirmations` to decide whether to execute a request: [1](#0-0) 

Membership is tracked separately in `self.members: UnorderedSet<MultisigMember>` and deletion is handled by `delete_member`: [2](#0-1) 

`delete_member` only removes **requests created by** the deleted member (`r.member == member`) and clears `num_requests_pk` for that member. It never iterates `self.confirmations` to strip the deleted member's identifier (`member.to_string()`) from the confirmation sets of *other* pending requests that the deleted member had already confirmed. The confirmation entry silently survives as a stale "vote" that is indistinguishable from a live member's vote once counted by `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm()`.

This mirrors the reported bug class exactly: a status/record (`isAddressCompromised` in the report; here, "confirmed by member X" in `self.confirmations`) that should be re-derived or cleared whenever the underlying entity's authorization changes (address restored / member deleted), but instead persists and is trusted at face value.

The binding that should hold is:
```
confirmations counted for request R == confirmations by members currently in self.members
```
After a member is deleted, this equality breaks for any request that member had previously confirmed but did not create.

### Impact Explanation
This is Critical impact per the multisig category: "a multisig request executed below threshold." A pending `Transfer`, `AddKey`, `FunctionCall`, or other `MultiSigRequestAction` can be executed with fewer *live* confirming members than `num_confirmations` mandates, because a removed member's stale confirmation is still tallied. This directly undermines the multisig's core security guarantee (k-of-n authorization) and can lead to unauthorized transfers of NEAR or unauthorized key/contract changes.

### Likelihood Explanation
This requires no attacker privilege beyond the normal operation of the multisig: any time a member is removed via the standard `DeleteMember` governance action (e.g., because the key was compromised — the same trigger as the original report) while they have outstanding confirmations on other pending requests, the vulnerability is live. No malicious actor or redeploy is needed; it is a natural consequence of ordinary member turnover combined with in-flight requests, making it straightforward to trigger.

### Recommendation
When executing `DeleteMember`, iterate all entries in `self.confirmations` and remove the deleted member's identifier (`member.to_string()`) from every request's confirmation set, not just from requests the deleted member created. Alternatively, re-validate at `confirm()` time (and before executing a request) that every string in the request's confirmation set still corresponds to a current member in `self.members`, discarding stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A.add_request_and_confirm(Transfer{amount, receiver_id: attacker})` → request `R` created, `confirmations(R) = {A}`.
3. `B.confirm(R)` → `confirmations(R) = {A, B}` (2/3, not yet executable).
4. The group legitimately submits and confirms a separate `DeleteMember{member: B}` request (3-of-4 confirmed by `A, C, D`) because `B`'s key is suspected compromised. `delete_member` (`multisig2/src/lib.rs:356-379`) removes `B` from `self.members` and deletes requests created by `B`, but request `R` (created by `A`) is untouched — `confirmations(R)` still contains `B`'s stale entry.
5. `C.confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations` in `confirm()` (`multisig2/src/lib.rs:299-309`) → `execute_request(R)` runs the `Transfer`, even though only `A` and `C` are live confirming members — one short of the required 3-of-4 threshold, because `B`'s revoked vote was still counted.

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
