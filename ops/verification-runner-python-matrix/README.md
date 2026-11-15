# Isolated verification runner: offline Python 3.10/3.12

The owner-scoped JIT `poststeward-verification` runner already has pinned Python 3.12.16, but the Python 3.10 CI matrix attempted an outbound `actions/python-versions` release download that repeatedly aborted in run 43121882888. This Dockerfile preserves the previously verified immutable container as its base, copying only CPython 3.10.22 from the already installed release runner image into the normal GitHub Actions toolcache. It does **not** make the verification runner a release runner.

Build on the authorised owner host with both immutable source images already available:

```sh
bash ops/verification-runner-python-matrix/build.sh
```

Before integrating, independently check the resulting image digest, toolcache marker, `python3.10 -m pip --version`, Python 3.12, and run a real, exact-source matrix CI job. Keep JIT eligibility, one-job isolation, network namespace, dropped capabilities, restricted resources, zero host-socket mounts, and fail-closed fork routing unchanged. A local service update must be atomic and only while no job is running; preserve a private rollback copy of the listener configuration. Deploying this image never grants production or provider-effect authority. Do not claim CI acceptance until the GitHub run itself completes successfully.
