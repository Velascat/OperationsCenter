# PlatformManifest Consumption

OperationsCenter consumes RepoGraph-backed PlatformManifest and PrivateManifest
data as topology and visibility metadata. It does not own canonical graph
semantics, and it does not redefine public/private disclosure policy.

## Boundary

```text
RepoGraph owns what exists and what may be disclosed.
PlatformManifest publishes the public graph instance.
PrivateManifest publishes the private graph instance and boundary artifact.
CxRP owns execution/routing contract semantics.
RxP owns runtime invocation semantics.
OperationsCenter owns governance and orchestration implementation.
ExecutorRuntime performs runtime invocation for OperationsCenter.
PlatformDeployment (current repo: PlatformDeployment) deploys and hosts runtime environments.
Managed private projects remain separate from OperationsCenter.
Custodian detects leaks and hygiene violations against declared policy.
```

Within OperationsCenter, the consumption boundary is narrow:

* `repo_graph_factory.build_effective_repo_graph()` composes the bundled
  PlatformManifest base plus optional private, project/work-scope, and local layers.
* `repo_graph_factory.build_effective_repo_graph_from_settings()` resolves
  operator-configured paths and degrades to `None` on manifest failures.
* Downstream OC consumers receive a `RepoGraph` or `None`. They do not parse
  manifest YAML directly.

## What OperationsCenter Reads

OperationsCenter reads RepoGraph-backed manifest data for:

* canonical repo identity
* public/private visibility
* private-manifest layering semantics
* project and work-scope attachment
* contract impact analysis
* local annotations resolved through PlatformDeployment/PlatformDeployment discovery

OperationsCenter does not read PlatformManifest to define:

* `TaskProposal` semantics
* `LaneDecision` semantics
* `ExecutionRequest` semantics
* `RuntimeInvocation` semantics
* backend runtime protocol details

Those stay in CxRP, RxP, SwitchBoard, and ExecutorRuntime respectively.

## Base Manifest Rule

OperationsCenter always uses the bundled `platform-manifest` package base:

```text
default_config_path() -> bundled PlatformManifest base
load_effective_graph(base, project=..., work_scope=..., local=...)
```

OC may choose the second and third composition layers, but it does not
replace the platform base with an OC-private fork at runtime.

This is intentional:

* platform graph semantics remain owned by RepoGraph
* public/private visibility rules remain centralized
* OC stays a consumer rather than a hidden schema authority

## Runtime Split

The execution split remains:

1. OperationsCenter validates and plans against CxRP-facing proposal and
   execution contracts.
2. SwitchBoard returns the routing decision.
3. OperationsCenter binds and orchestrates execution.
4. ExecutorRuntime performs runtime invocation using RxP semantics.
5. PlatformDeployment hosts and deploys the runtime environment.

That split matters because PlatformDeployment is not the OC backend, and
ExecutorRuntime is not the topology owner.

## Managed Projects

Managed projects remain external to OperationsCenter.
OC may manage, audit, or orchestrate them, and may consume their artifact
manifests and reports, but it must not absorb their project ontology into OC
core or treat them as internal OC subsystems.

Managed private projects remain outside the OperationsCenter platform core.
