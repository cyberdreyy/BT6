## Title
Stale confirmations from removed multisig members allow request execution below the configured confirmation threshold - (File: `multisig2/src/lib.rs`)

## Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges outstanding requests and confirmations that were *created* by the member being removed. It never scans the `confirmations` map to strip that member's approvals from *other* pending requests they had already confirmed. Once the member is removed, their stale confirmation remains counted, so a request can later be executed by `confirm` with fewer distinct, currently-valid members than `num_confirmations` requires.

## Finding Description
`confirm` decides whether to execute a request purely by comparing the size of the `confirmations: HashSet<String>` for that `request_id` against `self.num_confirmations`: [1](#0-0) 

Membership in that hash set is never revalidated against the current `members` set at confirmation time — only at the point each individual confirming call is made (`assert_valid_request` checks the *caller* is a current member, not that every previously stored confirmation still belongs to a current member).

`delete_member` is the only place that removes entries from `confirmations`, and it does so only for requests whose *creator* (`r.member`) equals the removed member: [2](#0-1) 

It does not iterate `self.confirmations` to remove the departing member's `to_string()` entry from the confirmation sets of *other* requests that member had confirmed but not created. As a result, once a member who confirmed request R is deleted from the multisig, R's `confirmations` HashSet still contains that now-invalid member's id, and that count is still added toward `self.num_confirmations` in the next `confirm` call.

This breaks the intended binding: `confirmations counted == live/authorized members who approved`. Instead, `confirmations counted` can exceed the count of members who are actually still members at execution time, letting `num_confirmations` be satisfied with only `num_confirmations - 1` (or fewer) live approvers.

## Impact Explanation
This is a Critical-impact issue per the multisig threshold guarantee: a multisig request can be executed with fewer live approvals than the configured `num_confirmations` k-of-n threshold. Since `MultiSigRequestAction` includes `Transfer`, `AddKey`, `FunctionCall`, and `DeployContract`, an attacker who arranges (or exploits an already-scheduled) member removal after partial confirmation can get funds transferred, keys added, or arbitrary code deployed to the multisig account with less than the required threshold of live signers actually agreeing at execution time — directly undermining the security guarantee the K-of-N scheme is designed to provide.

## Likelihood Explanation
No special privilege is required beyond normal multisig operation: member rotation/removal (`DeleteMember`) is an expected, routine action supported by the contract itself. Any workflow where a member confirms a pending request and is later removed (e.g., rotating out a compromised or departing signer, or a malicious/compromised member deliberately confirming requests before being removed) triggers the bug — no test/mock, redeploy, or malicious external actor is needed, only ordinary sequencing of `confirm` and `DeleteMember` requests that the contract's own API allows.

## Recommendation
When removing a member in `delete_member`, iterate over all entries in `self.confirmations` and remove the departing member's `to_string()` id from every confirmation set (not just requests they created), or re-validate at `confirm` time that every id in the stored confirmation set is still present in `self.members` before counting it toward the threshold.

## Proof of Concept
1. Multisig initialized with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create transfer request `R` (receiver attacker-controlled or arbitrary receiver).
3. `B` calls `confirm(R)` → `confirmations = {A, B}` (2 < 3, not yet executed).
4. Separately, a legitimate governance request removes member `B` via `DeleteMember` (e.g., key rotation) — `delete_member` only clears requests *created by* `B`; `R`'s confirmations (`{A, B}`) are untouched because `R` was created by `A`, not `B`.
5. `C` calls `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations`, so `execute_request(R)` runs and the transfer executes.
6. Only two currently-valid members (`A` and `C`) actually approved at execution time — `B`'s stale confirmation counted toward the 3-of-4 threshold even though `B` is no longer a member — confirming a request below the intended live-member threshold. [3](#0-2) [4](#0-3)

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
