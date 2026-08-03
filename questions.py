import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = "gitlab-org/gitlab-runner"
# todo: the name of the repository
REPO_NAME = "gitlab-runner"

run_number = os.environ.get("GITHUB_RUN_NUMBER", "0")


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Core build/job model, variable handling, and log/secret boundaries
    # =================================================================================
    "common/build.go",
    "common/build_settings.go",
    "common/build_step_dispatch.go",
    "common/config.go",
    "common/network.go",
    "common/secrets.go",
    "common/allowed_images.go",
    "common/shell.go",
    "common/executor.go",
    "common/trace.go",
    "common/environment_key.go",
    "common/spec/spec.go",
    "common/spec/inputs.go",
    "common/spec/variables.go",
    "common/buildlogger/build_logger.go",
    "common/buildlogger/innerstream/innerstream.go",
    "common/buildlogger/internal/masker/masker.go",
    "common/buildlogger/internal/tokensanitizer/token_masker.go",
    "common/buildlogger/internal/urlsanitizer/urlsanitizer.go",

    # =================================================================================
    # Runner <-> GitLab transport, trace, and job state handling
    # =================================================================================
    "network/client.go",
    "network/gitlab.go",
    "network/requester.go",
    "network/retry_requester.go",
    "network/trace.go",
    "network/patch_response.go",
    "network/retry_tracker.go",

    # =================================================================================
    # Script generation and concrete job execution
    # =================================================================================
    "commands/multi.go",
    "commands/single.go",
    "commands/wrapper.go",
    "commands/steps/steps.go",
    "commands/steps/recovery.go",
    "commands/tracing.go",
    "commands/helpers/proxy_exec.go",
    "functions/concrete/concrete.go",
    "functions/concrete/run/runner.go",
    "functions/concrete/run/run_steps.go",
    "functions/concrete/run/env/env.go",
    "functions/concrete/run/stages/get_sources.go",
    "functions/concrete/run/stages/artifact_download.go",
    "functions/concrete/run/stages/artifact_upload.go",
    "functions/concrete/run/stages/cache_extract.go",
    "functions/concrete/run/stages/cache_archive.go",
    "functions/concrete/run/stages/cleanup.go",
    "functions/concrete/run/stages/step.go",
    "functions/concrete/run/stages/internal/retry/retry.go",
    "functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go",
    "functions/concrete/builder/builder.go",
    "functions/concrete/builder/options.go",
    "functions/concrete/builder/variables/variables.go",
    "functions/script_legacy/internal/script_generator.go",
    "functions/script_legacy/internal/escape.go",
    "functions/script_legacy/internal/shell.go",
    "functions/script_legacy/internal/script_header.go",
    "functions/script_legacy/internal/normalize_exit_error.go",
    "functions/script_legacy/internal/command_processor.go",
    "functions/script_legacy/internal/executor.go",
    "functions/script_legacy/internal/trace_section.go",
    "functions/script_legacy/internal/command_formatter.go",
    "functions/script_legacy/script_legacy.go",
    "steps/execute.go",
    "steps/steps.go",
    "steps/localserver/localserver.go",
    "shells/abstract.go",
    "shells/bash.go",
    "shells/powershell.go",
    "shells/proxy_exec.go",
    "shells/shell_writer.go",
    "shells/trap_command_exit_status.go",

    # =================================================================================
    # Artifacts, cache, archives, and path-handling boundaries
    # =================================================================================
    "commands/helpers/artifacts_downloader.go",
    "commands/helpers/artifacts_uploader.go",
    "commands/helpers/artifact_metadata.go",
    "commands/helpers/cache_archiver.go",
    "commands/helpers/cache_extractor.go",
    "commands/helpers/cache_client.go",
    "commands/helpers/cache_env.go",
    "commands/helpers/cache_init.go",
    "commands/helpers/cache_metadata.go",
    "commands/helpers/file_archiver.go",
    "commands/helpers/internal/store/store.go",
    "commands/helpers/internal/store/store_unix.go",
    "commands/helpers/internal/store/store_windows.go",
    "commands/helpers/archive/archive.go",
    "commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go",
    "commands/helpers/archive/fastzip/zip_fastzip_archiver.go",
    "commands/helpers/archive/fastzip/zip_fastzip_extractor.go",
    "commands/helpers/archive/tarzstd/tarzstd_archiver.go",
    "commands/helpers/archive/tarzstd/tarzstd_extractor.go",
    "commands/helpers/archive/tarzstd/ops_unix.go",
    "commands/helpers/archive/tarzstd/ops_windows.go",
    "commands/helpers/archive/ziplegacy/zip_legacy_archiver.go",
    "commands/helpers/archive/ziplegacy/zip_legacy_extractor.go",
    "commands/helpers/archive/raw/raw_archiver.go",
    "helpers/archives/path_check_helper.go",
    "helpers/archives/path_error_tracker.go",
    "helpers/archives/gzip_create.go",
    "helpers/archives/zip_create.go",
    "helpers/archives/zip_extract.go",
    "helpers/archives/zip_extra.go",
    "helpers/archives/zip_extra_unix.go",
    "helpers/archives/zip_extra_windows.go",
    "helpers/archives/os_unix.go",
    "helpers/archives/os_windows.go",
    "cache/cache.go",
    "cache/adapter.go",
    "cache/cachekey/cachekey.go",
    "cache/cacheconfig/cacheconfig.go",
    "cache/credentials_adapter.go",
    "cache/s3/adapter.go",
    "cache/s3/minio.go",
    "cache/s3/bucket_location_tripper.go",
    "cache/s3/credentials_adapter.go",
    "cache/s3v2/adapter.go",
    "cache/s3v2/s3.go",
    "cache/gcs/adapter.go",
    "cache/gcs/credentials_resolver.go",
    "cache/gcsv2/adapter.go",
    "cache/azure/adapter.go",
    "cache/azure/azure.go",
    "cache/azure/credentials_resolver.go",

    # =================================================================================
    # Docker executor and non-privileged container isolation
    # =================================================================================
    "executors/abstract.go",
    "executors/default_executor_provider.go",
    "executors/environment.go",
    "executors/executors.go",
    "executors/init.go",
    "executors/docker/docker.go",
    "executors/docker/docker_command.go",
    "executors/docker/services.go",
    "executors/docker/steps.go",
    "executors/docker/pull.go",
    "executors/docker/provider.go",
    "executors/docker/network.go",
    "executors/docker/volume.go",
    "executors/docker/config_updater.go",
    "executors/docker/labeler.go",
    "executors/docker/environment_key_fields.go",
    "executors/docker/terminal.go",
    "executors/docker/tty.go",
    "executors/docker/internal/pull/manager.go",
    "executors/docker/internal/networks/manager.go",
    "executors/docker/internal/networks/utils.go",
    "executors/docker/internal/exec/exec.go",
    "executors/docker/internal/user/user.go",
    "executors/docker/internal/prebuilt/prebuilt.go",
    "executors/docker/internal/volumes/manager.go",
    "executors/docker/internal/volumes/utils.go",
    "executors/docker/internal/volumes/permission/set.go",
    "executors/docker/internal/volumes/permission/linux_set.go",
    "executors/docker/internal/volumes/permission/windows_set.go",
    "executors/docker/internal/volumes/parser/base_parser.go",
    "executors/docker/internal/volumes/parser/errors.go",
    "executors/docker/internal/volumes/parser/parser.go",
    "executors/docker/internal/volumes/parser/volume.go",
    "executors/docker/internal/volumes/parser/linux_parser.go",
    "executors/docker/internal/volumes/parser/windows_parser.go",
    "executors/docker/internal/volumes/parser/windows_path.go",
    "executors/docker/internal/volumes/parser/windows_path_windows.go",

    # =================================================================================
    # Kubernetes executor, pod overwrites, and identity boundaries
    # =================================================================================
    "executors/kubernetes/kubernetes.go",
    "executors/kubernetes/exec.go",
    "executors/kubernetes/overwrites.go",
    "executors/kubernetes/steps.go",
    "executors/kubernetes/steps_pod.go",
    "executors/kubernetes/util.go",
    "executors/kubernetes/service_proxy.go",
    "executors/kubernetes/container_entrypoint_forwarder.go",
    "executors/kubernetes/host_aliases.go",
    "executors/kubernetes/log_processor.go",
    "executors/kubernetes/provider.go",
    "executors/kubernetes/terminal.go",
    "executors/kubernetes/feature.go",
    "executors/kubernetes/internal/pull/manager.go",
    "executors/kubernetes/internal/pull/errors.go",
    "executors/kubernetes/internal/watchers/informer_factory.go",
    "executors/kubernetes/internal/watchers/pod.go",

    # =================================================================================
    # Other supported executors
    # =================================================================================
    "executors/shell/shell.go",
    "executors/shell/steps.go",
    "executors/shell/shell_terminal.go",
    "executors/ssh/ssh.go",
    "executors/instance/instance.go",
    "executors/instance/steps.go",
    "executors/custom/custom.go",
    "executors/custom/config.go",
    "executors/custom/consts.go",
    "executors/custom/terminal.go",
    "executors/custom/api/config.go",
    "executors/custom/api/const.go",
    "executors/custom/command/command.go",
    "executors/custom/command/errors.go",

    # =================================================================================
    # Secrets backends, helper identity, session, terminal, and router paths
    # =================================================================================
    "helpers/docker/credentials.go",
    "helpers/docker/options.go",
    "helpers/docker/auth/auth.go",
    "helpers/docker/client.go",
    "helpers/docker/errors/errors.go",
    "helpers/docker/official_docker_client.go",
    "helpers/secrets/errors.go",
    "helpers/secrets/resolvers/gitlab_secrets_manager/resolver.go",
    "helpers/secrets/resolvers/gcp_secret_manager/resolver.go",
    "helpers/secrets/resolvers/azure_key_vault/azure_key_vault_resolver.go",
    "helpers/secrets/resolvers/aws/aws_secrets_manager_resolver.go",
    "helpers/secrets/resolvers/vault/resolver.go",
    "helpers/gitlab_secrets_manager/service/gitlab_secrets_manager.go",
    "helpers/gcp_secret_manager/service/gcp_secret_manager.go",
    "helpers/azure_key_vault/service/azure_key_vault.go",
    "helpers/aws/service/aws_service.go",
    "helpers/vault/auth.go",
    "helpers/vault/client.go",
    "helpers/vault/result.go",
    "helpers/vault/secret_engine.go",
    "helpers/vault/utils.go",
    "helpers/vault/auth_methods/data.go",
    "helpers/vault/auth_methods/registry.go",
    "helpers/vault/auth_methods/jwt/auth.go",
    "helpers/vault/secret_engines/operations.go",
    "helpers/vault/secret_engines/registry.go",
    "helpers/vault/secret_engines/generic/engine.go",
    "helpers/vault/secret_engines/kv_v2/engine.go",
    "helpers/vault/internal/registry/registry.go",
    "helpers/vault/service/vault.go",
    "helpers/certificate/certificate.go",
    "helpers/certificate/x509.go",
    "helpers/tls/consts.go",
    "helpers/tls/ca_chain/builder.go",
    "helpers/tls/ca_chain/helpers.go",
    "helpers/tls/ca_chain/resolver.go",
    "helpers/tls/ca_chain/resolver_chain.go",
    "helpers/tls/ca_chain/resolver_url.go",
    "helpers/tls/ca_chain/resolver_verify.go",
    "helpers/container/helperimage/info.go",
    "helpers/container/helperimage/linux_info.go",
    "helpers/container/helperimage/windows_info.go",
    "helpers/container/services/services.go",
    "helpers/path.go",
    "helpers/path/unix_path.go",
    "helpers/path/windows_path.go",
    "helpers/url/gitauth.go",
    "helpers/url/clean_url.go",
    "helpers/transfer/content_range.go",
    "helpers/transfer/parallel_download.go",
    "helpers/pull_policies/pull_policies.go",
    "helpers/shell_escape.go",
    "helpers/shorten_token.go",
    "helpers/process/commander.go",
    "helpers/process/job_unix.go",
    "helpers/process/job_windows.go",
    "helpers/process/killer.go",
    "helpers/process/killer_unix.go",
    "helpers/process/killer_windows.go",
    "helpers/runner_wrapper/wrapper.go",
    "helpers/runner_wrapper/wrapper_unix.go",
    "helpers/runner_wrapper/wrapper_windows.go",
    "helpers/runner_wrapper/commander.go",
    "helpers/runner_wrapper/commander_unix.go",
    "helpers/runner_wrapper/commander_windows.go",
    "helpers/runner_wrapper/api/init_graceful_shutdown_request.go",
    "helpers/runner_wrapper/api/errors.go",
    "helpers/runner_wrapper/api/server/server.go",
    "helpers/runner_wrapper/api/shutdown_callback.go",
    "helpers/runner_wrapper/api/status.go",
    "helpers/runner_wrapper/api/client/options.go",
    "helpers/runner_wrapper/api/client/backoff.go",
    "helpers/runner_wrapper/api/client/client.go",
    "helpers/runner_wrapper/api/client/target.go",
    "session/server.go",
    "session/session.go",
    "session/proxy/proxy.go",
    "session/terminal/terminal.go",
    "router/token_creds.go",
    "router/client.go",
    "router/client_conn_factory.go",
    "router/internal/wstunnel/client.go",
    "router/internal/wstunnel/netconn.go",
    "apps/gitlab-runner-helper/main.go",
]


target_scopes = [
    "Critical. An unprivileged CI job escapes non-privileged executor isolation and gains code execution on the runner host, helper, or another tenant workload",
    "Critical. An unprivileged CI job reads, reuses, or exfiltrates another project or job's CI_JOB_TOKEN, masked/protected variables, resolved secrets, registry credentials, or cache credentials",
    "Critical. An unprivileged CI job reads, writes, or deletes files outside its intended build, cache, or artifact roots via path traversal, archive extraction, symlink, cleanup, or volume logic",
    "Critical. An unprivileged CI job bypasses runner-enforced restrictions on images, services, pull policies, users, service accounts, namespaces, volumes, or pod/container settings and gains stronger permissions or identity than configured",
    "Critical. An unprivileged CI job poisons or exfiltrates another project or job's cache, artifacts, checkout state, or helper state across tenant boundaries",
    "Critical. A session, terminal, proxy, or router bug lets one unprivileged job attach to, hijack, or execute commands in another job",
    "High. Script generation, variable expansion, quoting, or helper execution causes runner-side command execution or unintended commands outside the authored job payload",
    "High. Runner-to-GitLab or runner-to-backend auth logic lets a normal project user impersonate another job, alter job state, or access unauthorized project resources",
    "High. Secret masking, trace handling, or log sanitization exposes protected values to users or projects that should not receive them",
    "Medium. A single normal job can cause persistent multi-tenant runner disruption that survives job cancellation or affects other projects; exclude generic one-job DoS and admin-chosen insecure setups",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one GitLab Runner target.

    ```
    target_file format:
    "'File Name: executors/kubernetes/overwrites.go -> Scope: Critical restriction bypass'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact GitLab Runner target:

    {target_file}

    Project focus:
    GitLab Runner polls jobs from GitLab and executes them through docker, kubernetes, shell, ssh, instance, and custom executors. The main security boundary is between a normal GitLab user or CI job and the runner host, helper containers, other projects/jobs, secrets, cache/artifacts, and executor identities.

    Core invariants:
    * A normal job must stay confined to its own workspace, tokens, secrets, logs, cache, artifacts, session, and executor sandbox.
    * Runner-enforced restrictions on image/service choice, pull policy, users, service accounts, namespaces, volumes, and pod/container settings must not be bypassable by job input.
    * Job-controlled paths, archives, variables, traces, and scripts must not cause host file access, command injection, or cross-project impact.
    * Protected or masked values must never leak through traces, logs, artifacts, cache, helper flows, or session traffic.
    * Runner/GitLab auth state must not let one job impersonate another or access another project's resources.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Go symbols when possible.
    * Attacker is unprivileged: a normal GitLab user or pipeline author who can trigger a job or control job inputs accepted by Runner.
    * Never rely on runner admin, GitLab admin, cluster admin, privileged containers, host PID mode, shell executor trust on a shared host, malicious peers/nodes, leaked keys, or insecure settings explicitly chosen by admins.
    * Generate 10 to 15 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, path, isolation, auth, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.

    High-value attack surfaces:
    * Build/job spec, variables, secrets resolution, and trace/log masking.
    * Artifact/cache archive create/extract and path handling.
    * Shell generation and quoting on bash and PowerShell.
    * Docker/Kubernetes executor isolation, image/service restrictions, helper behavior, and overwrite controls.
    * Runner network/auth flows, sessions, terminal/proxy/router paths, and backend credentials.
    * Custom/instance/ssh execution boundaries and cleanup paths.

    Impact mapping:
    * Executor breakout or cross-job takeover.
    * Cross-project secret/token exfiltration.
    * Unauthorized file access outside job roots.
    * Restriction bypass causing stronger identity or permissions.
    * Cross-project cache/artifact poisoning or read access.
    * Session/job hijack.
    * Persistent multi-tenant disruption only if it survives job cancellation or impacts other projects.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused GitLab Runner exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- The referenced GitLab Runner file/path exists. Do not say files are missing.
- Do not ask for code. Use available repository context.
- Analyze only this question and only the scoped impact.
- Attacker is unprivileged: a normal GitLab user or pipeline author controlling job inputs accepted by Runner.
- Ignore admin-only, leaked-key, docs, style, best-practice, and purely theoretical issues.
- Privileged functions matter only if they create a later user-triggered exploit path.
- Reject findings that only restate documented insecure admin choices such as privileged containers, host PID mode, docker.sock exposure, or shell executor trust on a shared host.
- Do not rely on malicious peers/nodes, cluster-admin compromise, GitLab-admin compromise, or external service compromise alone.

## Mission
Prove or disprove this as a real GitLab Runner bug.

Check:
- exact reachable Go path;
- attacker-controlled inputs (job variables, CI config fields, cache/artifact names, archive contents, image/service definitions, trace/session traffic, secret references);
- state changes before/after external calls, helper actions, archive extraction, or cross-module interaction;
- whether existing checks (allowed images, overwrite guards, path validation, masking, auth checks, cleanup logic) stop it;
- whether the scoped impact is concrete;
- whether a Go unit/integration test, fuzz test, or PoC job can reproduce it.

## Core Invariants
- A normal job must not escape its executor sandbox or access another project's workload.
- Secrets, tokens, and masked values must not leak across jobs, projects, logs, traces, caches, artifacts, or sessions.
- File operations must stay within intended build/cache/artifact roots.
- Runner-enforced restrictions on images, services, pull policies, users, service accounts, namespaces, and volumes must hold against user-controlled input.
- Job, session, trace, and backend auth state must not let one job impersonate another.

## Valid Only If
1. Exact file/function/line range exists.
2. Root cause is a real missing check, unsafe parsing, broken isolation boundary, bad auth decision, path bug, or logic error.
3. Exploit path is: preconditions -> attacker action/data -> trigger -> bad state/result.
4. Existing protections are reviewed and insufficient.
5. Impact matches the scoped impact.
6. PoC/test idea has clear assertions.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Go unit/integration test, fuzz test, or PoC job plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for GitLab Runner security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject admin-only, runner-admin-only, cluster-admin-only, leaked-key, best-practice, docs/style, generic misconfiguration, and purely theoretical issues.
- Reject if the exploit requires privileged containers, docker.sock exposure, host PID mode, shell executor trust on a shared host, malicious peers/nodes, or unsupported deployment assumptions.
- A valid report must be triggerable by an unprivileged user, unless the claim proves privilege escalation from a user path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Prefer critical cross-boundary findings; generic one-job DoS is out unless the report proves persistent multi-tenant disruption.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken isolation/auth/path/masking assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: unit PoC, integration PoC, fuzz/invariant test, or exact manual steps.
7. No obvious rejection reason from SECURITY.md, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal GitLab user or pipeline author trigger this without runner-admin or cluster-admin help?
- Does the code actually behave as claimed?
- Is the impact caused by GitLab Runner logic, not only by an explicitly insecure admin setup?
- Is the cross-boundary impact concrete, not hypothetical?
- Would a bounty triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or a Go unit/integration/fuzz test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for GitLab Runner.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat in-scope GitLab Runner files as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.

## Objective
Find whether the same vulnerability class can occur in GitLab Runner's in-scope code.
Use the external report as a hint, not as proof.

Note: Check the SECURITY.md and think in this actual way.
Note: Never generate a report that would result in an out-of-scope and rejected vulnerability.

## Method
1. Classify vuln type (auth, path traversal, archive extraction, secret leak, sandbox escape, restriction bypass, impersonation, session hijack, persistent DoS).
2. Map the vulnerability pattern to GitLab Runner architecture to find a valid analog.
3. Prove root cause with exact file/function/line references in the GitLab Runner codebase.
4. Confirm concrete impact + realistic likelihood within the GitLab Runner environment.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Trusted-role compromise required.
- Report depends on privileged containers, host PID mode, docker.sock exposure, shell executor trust on a shared host, malicious peers/nodes, or other admin-chosen insecure settings.
- Theoretical-only issue with no concrete project impact.
- Impact or likelihood missing.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
