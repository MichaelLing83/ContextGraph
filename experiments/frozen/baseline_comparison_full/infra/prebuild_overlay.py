#!/usr/bin/env python3
"""Pre-build SWE-agent overlay images for failed instances."""
import subprocess, json, time

with open('/home/jie/codes/ContextGraph/results/baseline_comparison_full/failed_105.json') as f:
    data = json.load(f)
ids = data['instance_ids']

# The Dockerfile that swerex uses (from docker.py)
# Uses $BASE_IMAGE ARG and $(nproc) shell expansion
DOCKERFILE = (
    "ARG BASE_IMAGE\n\n"
    "FROM python:3.11.9-slim-bookworm AS builder\n"
    "RUN apt-get update && apt-get install -y "
    "wget gcc make zlib1g-dev libssl-dev "
    "&& rm -rf /var/lib/apt/lists/*\n"
    "WORKDIR /build\n"
    "RUN wget https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tgz "
    "&& tar xzf Python-3.11.8.tgz\n"
    "WORKDIR /build/Python-3.11.8\n"
    "RUN ./configure "
    "--prefix=/root/python3.11 "
    "--enable-shared "
    'LDFLAGS="-Wl,-rpath=/root/python3.11/lib" && '
    "make -j$(nproc) && "
    "make install && "
    "ldconfig\n\n"
    "FROM $BASE_IMAGE\n"
    "RUN apt-get update && apt-get install -y "
    "libc6 "
    "&& rm -rf /var/lib/apt/lists/*\n"
    "COPY --from=builder /root/python3.11 /root/python3.11\n"
    "ENV LD_LIBRARY_PATH=/root/python3.11/lib:${LD_LIBRARY_PATH:-}\n"
    "RUN /root/python3.11/bin/python3 --version\n"
    "RUN /root/python3.11/bin/pip3 install --no-cache-dir swe-rex\n\n"
    "RUN ln -s /root/python3.11/bin/swerex-remote /usr/local/bin/swerex-remote\n\n"
    "RUN swerex-remote --version\n"
)

success = 0
fail = 0
failed_ids = []
total = len(ids)
start = time.time()

for i, iid in enumerate(ids):
    docker_name = iid.replace('__', '_1776_')
    base_image = f'swebench/sweb.eval.x86_64.{docker_name}:latest'.lower()

    elapsed = time.time() - start
    print(f'[{i+1}/{total}] {iid}...', end=' ', flush=True)

    # Pull base image
    subprocess.run(['docker', 'pull', base_image], capture_output=True)

    # Build overlay
    result = subprocess.run(
        ['docker', 'build', '-q', '--build-arg', f'BASE_IMAGE={base_image}', '-'],
        input=DOCKERFILE.encode(),
        capture_output=True,
    )

    if result.returncode == 0:
        success += 1
        print(f'OK ({elapsed:.0f}s total)')
    else:
        fail += 1
        failed_ids.append(iid)
        err = result.stderr.decode()[-150:]
        print(f'FAIL: {err[:80]}')

elapsed = time.time() - start
print()
print(f'Done! Total: {total}, Success: {success}, Failed: {fail}, Time: {elapsed:.0f}s ({elapsed/60:.1f}min)')
if failed_ids:
    print(f'Failed instances ({len(failed_ids)}):')
    for fid in failed_ids:
        print(f'  {fid}')
