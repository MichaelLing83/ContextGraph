# Derived image: jiayuanz3/swecontextbench:<tag> + portable Python 3.11 + swe-rex + patch
# Usage:
#   docker build --build-arg BASE_IMAGE=jiayuanz3/swecontextbench:astropy.astropy-15082 \
#                -t swectx-patched:astropy.astropy-15082 \
#                -f swectx_patched.Dockerfile .
#
# The patch forces bash to be spawned with -i (interactive) so PS1 is reliably
# echoed via the PTY. Without -i, pexpect on Python 3.11 inside these
# jiayuanz3 images hangs 30s waiting for SHELLPS1PREFIX even though the
# command runs.

ARG BASE_IMAGE

FROM python:3.11.9-slim-bookworm AS builder
RUN apt-get update && apt-get install -y \
    wget gcc make zlib1g-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN wget -q https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz \
    && tar xzf Python-3.11.9.tgz
WORKDIR /build/Python-3.11.9
RUN ./configure --prefix=/root/python3.11 --enable-shared \
    LDFLAGS='-Wl,-rpath=/root/python3.11/lib' \
    && make -j$(nproc) >/tmp/make.log 2>&1 \
    && make install >>/tmp/make.log 2>&1 \
    && ldconfig

FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y libc6 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/python3.11 /root/python3.11
ENV LD_LIBRARY_PATH=/root/python3.11/lib:${LD_LIBRARY_PATH:-}
RUN /root/python3.11/bin/python3 --version
RUN /root/python3.11/bin/pip3 install --no-cache-dir swe-rex
RUN ln -s /root/python3.11/bin/swerex-remote /usr/local/bin/swerex-remote
# Patch swerex for jiayuanz3/swecontextbench compatibility:
#   1. Default CreateBashSessionRequest.startup_timeout was 1.0s — too short
#      for these conda-heavy images where bash -i + activation runs longer
#      than 1s. SWE-agent doesn't pass a custom timeout, so we raise the
#      default to 30s in-image.
#   2. Spawn with `bash -i` so PS1 actually prints through the PTY.
#   3. Also unset PROMPT_COMMAND in reset commands (defensive — conda
#      sometimes uses PROMPT_COMMAND to re-set PS1 every prompt).
RUN python3 -c "import pathlib; \
    p = pathlib.Path('/root/python3.11/lib/python3.11/site-packages/swerex/runtime/local.py'); \
    s = p.read_text(); \
    new = s.replace('\"/usr/bin/env bash\"', '\"/usr/bin/env\", args=[\"bash\", \"-i\"]'); \
    assert new != s, 'spawn patch site not found'; \
    new2 = new.replace('f\"export PS1=', 'f\"unset PROMPT_COMMAND ; export PS1=', 1); \
    assert new2 != new, 'PROMPT_COMMAND patch site not found'; \
    new3 = new2.replace('timeout=self.request.startup_timeout', 'timeout=max(self.request.startup_timeout, 30.0)'); \
    assert new3 != new2, 'startup_timeout override site not found'; \
    p.write_text(new3); \
    print('local.py: bash -i + unset PROMPT_COMMAND + min-30s-timeout patches applied')"
RUN swerex-remote --version
