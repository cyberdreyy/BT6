## Title
Stale confirmations from removed multisig members still count toward the approval threshold, allowing execution below the live-member K-of-N threshold - (File: `multisig2/src/lib.rs`)

## Summary
The report's underlying bug class — checks are enforced when a request is *created* but not re-validated when it is *settled/executed* — has a direct, exploitable analog in `multisig2/src/lib.rs` (and equivalently in `multisig/src/lib.rs`). When a member confirms a pending request, their confirmation is recorded as a bare string in the `confirmations` set. If that member is later removed from the multisig via a separate `DeleteMember` request, their prior confirmation is only purged from requests **they themselves originally created** — not from other pending requests they had merely confirmed. The stale confirmation therefore continues to count toward `num_confirmations` when the request is finally settled, letting a request execute with fewer *live* member approvals than the configured threshold.

## Finding Description
`confirm()` checks membership only for the *calling* signer via `assert_valid_request` → `current_member()`, and simply increments a `HashSet<String>` of confirmer identities: [1](#0-0) 

The threshold comparison (`confirmations.len() as u32 + 1 >= self.num_confirmations`) trusts the *count* of strings in that set, never re-checking that each recorded confirmer is still a current member of `self.members`.

`delete_member()` only cleans up confirmations for requests whose *original creator* (`r.member`) equals the removed member — it does not scan/clean confirmations given by that member on requests created by someone else: [2](#0-1) 

So a member's confirmation on someone else's pending request survives that member's removal. This exactly matches the report's root cause: request-time validity checks (here, "signer is a current multisig member") are not re-verified at settlement time (`confirm`'s threshold check / `execute_request`), and the fund/contract state (membership) can change between confirmation and execution.

The binding broken is: `confirmations.len() (recorded) == live, currently-authorized member confirmations` — the code assumes equality, but removal of a member breaks it for requests they didn't author themselves.

## Impact Explanation
This allows an `execute_request` (which can include `Transfer`, `AddKey`, `DeployContract`, `FunctionCall`, `AddMember`, etc.) to run with effectively fewer live-member approvals than `num_confirmations` requires. This is a "multisig request executed below threshold" scenario — Critical impact per the stated criteria, since it undermines the K-of-N authorization guarantee that the entire contract is built on (funds transfer, key/member changes, contract upgrades can all be triggered this way).

## Likelihood Explanation
Requires: (1) a member confirms a request created by another member, (2) that confirming member is later removed from the multisig (a routine `DeleteMember` operation, e.g. for a compromised or departing member), and (3) the original request is still pending and gets one more live confirmation to cross what should be, but isn't, the live-member threshold. Member turnover combined with slow-to-confirm pending requests is a realistic operational pattern, making this reachable without any privileged bypass — only ordinary multisig usage (creating/confirming/removing members), which any member can help trigger.

## Recommendation
**Short-term:** When a member is removed (`delete_member`), purge that member's identity from the `confirmations` set of *every* pending request, not just requests they authored. Alternatively, re-validate at `confirm()` time (before comparing to threshold) that every entry in the `confirmations` set still corresponds to a current member of `self.members`, discarding stale ones (and recomputing whether the threshold is actually met by live members).

**Long-term:** Document that membership and confirmation validity must be re-checked at settlement/execution time, not only at creation/confirmation time, and add unit tests covering: confirm → remove confirming member → confirm again, asserting the request cannot execute below the live-member threshold.

## Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A` calls `add_request_and_confirm(Transfer{...})` → request `R1` has confirmations `{A}`.
3. `B` calls `confirm(R1)` → confirmations `{A, B}` (2 of 3, one short of executing).
4. Separately, `A`, `C`, `D` create and confirm a `DeleteMember{member: B}` request (3 confirmations from live members A/C/D) → executes; `B` is removed from `self.members` and its access key deleted. Per [3](#0-2)  only requests *authored by B* are cleaned up — `R1` (authored by `A`) keeps its stale `B` confirmation.
5. Members are now `{A, C, D}`, `num_confirmations` still 3. `C` calls `confirm(R1)` → `confirmations.len() == 3` (`A`, stale `B`, `C`) `>= 3` → `execute_request` runs the `Transfer`, even though only 2 live members (`A`, `C`) actually authorized it. [4](#0-3) [5](#0-4)

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
