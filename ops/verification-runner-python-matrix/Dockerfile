# Offline, digest-locked augmentation of the existing isolated verifier.
# The release image is only a source of the already-vetted CPython 3.10.22
# distribution. No credentials, Docker socket or release authority are copied.
FROM poststeward-release-runner:python310-312-uv-20261109 AS python310
FROM poststeward-verification-runner:python3.12.16-node24
USER root
COPY --from=python310 --chown=runner:runner /opt/poststeward/python310/ /opt/tools/Python/3.10.22/x64/
RUN touch /opt/tools/Python/3.10.22/x64.complete \
 && chown runner:runner /opt/tools/Python/3.10.22/x64.complete \
 && /opt/tools/Python/3.10.22/x64/bin/python3.10 --version \
 && /opt/tools/Python/3.10.22/x64/bin/python3.10 -m pip --version \
 && LD_LIBRARY_PATH=/opt/tools/Python/3.12.16/x64/lib /opt/tools/Python/3.12.16/x64/bin/python3.12 --version
USER runner
