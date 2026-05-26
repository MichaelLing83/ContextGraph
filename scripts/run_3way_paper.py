"""Orchestrate 2-condition (No-Context, ContextGraph-Summary) SWE-agent runs
on SWE-ContextBench Lite 99 Related instances.

Disk-aware: pulls one image, runs both conditions back-to-back against it,
then `docker rmi` to free disk before next instance. Resumable: skips
instances that already have .traj output in BOTH method dirs.

Output:
  results/swectx_paper/no_context/output/{instance_id}/{instance_id}.traj
  results/swectx_paper/contextgraph_summary/output/{instance_id}/{instance_id}.traj

Prereqs:
  - Memory server on http://127.0.0.1:8025 (already running, points at 7695)
  - configs/swe_agent_treatment.yaml + configs/swe_agent_control.yaml exist
  - Each instance JSON entry has image_name, instance_id, problem_statement,
    repo_name="testbed", base_commit
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "swectx_paper"
CONFIGS_DIR = PROJECT_ROOT / "configs"
# Linux Docker bridge gateway — overridable for non-default Docker setups.
DOCKER_HOST_IP = os.environ.get("DOCKER_HOST_IP", "172.17.0.1")
COST_LIMIT = 3.0


def gen_no_context_config(out: Path) -> Path:
    """SWE-agent control config with cost_limit set."""
    src = (CONFIGS_DIR / "swe_agent_control.yaml").read_text()
    src = src.replace("per_instance_cost_limit: 0",
                      f"per_instance_cost_limit: {COST_LIMIT}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src)
    return out


def gen_contextgraph_config(out: Path, port: int) -> Path:
    """SWE-agent treatment config pointing at MEMORY_SERVER_URL."""
    src = (CONFIGS_DIR / "swe_agent_treatment.yaml").read_text()
    src = src.replace(
        "      NEO4J_URI: bolt://neo4j-contextgraph:7687\n"
        "      NEO4J_USER: neo4j\n"
        "      NEO4J_PASSWORD: INJECTED_AT_RUNTIME",
        f"      MEMORY_SERVER_URL: http://{DOCKER_HOST_IP}:{port}",
    )
    src = src.replace("per_instance_cost_limit: 0",
                      f"per_instance_cost_limit: {COST_LIMIT}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src)
    return out


def docker_pull(image: str, retries: int = 5) -> bool:
    import random
    for attempt in range(retries):
        r = subprocess.run(
            ["docker", "pull", "--platform", "linux/amd64", image],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True
        err = (r.stderr + r.stdout).lower()
        if "manifest unknown" in err:
            return False
        if attempt < retries - 1:
            rate = any(s in err for s in ("429", "toomanyrequests", "rate limit"))
            delay = (30 if rate else 5) * (2 ** attempt) + random.uniform(0, 3)
            print(f"    pull failed, retry in {delay:.1f}s: {err[:120]}")
            time.sleep(delay)
    return False


def docker_image_exists(tag: str) -> bool:
    r = subprocess.run(["docker", "image", "inspect", tag],
                       capture_output=True, text=True)
    return r.returncode == 0


_DOCKER_TAG_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")


def _validate_docker_ref(ref: str, kind: str) -> None:
    """Reject image/tag strings that wouldn't be a valid docker reference.

    Defensive only — the caller already supplies controlled values from
    test99 JSON, but rejecting weird characters here means a typo'd field
    fails fast instead of being interpolated into a docker build/run.
    """
    if not _DOCKER_TAG_RE.match(ref or ""):
        raise ValueError(f"unsafe {kind} ref: {ref!r}")


def docker_build_patched(base: str, tag: str, dockerfile: str, cwd: str) -> bool:
    _validate_docker_ref(base, "base image")
    _validate_docker_ref(tag, "tag")
    cmd = ["docker", "build", "--build-arg", f"BASE_IMAGE={base}",
           "-t", tag, "-f", dockerfile, "."]
    # List-form invocation with validated refs; no shell interpretation.
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)  # nosec B603  # nosem
    if r.returncode != 0:
        print(f"    build FAILED:\n{r.stderr[-500:]}")
        return False
    return True


def docker_rmi(image: str) -> None:
    subprocess.run(["docker", "rmi", "-f", image],
                   capture_output=True, text=True)


def run_swe_agent(config: Path, single_json: Path, out_dir: Path, iid: str,
                  num_workers: int = 1) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "sweagent", "run-batch",
        "--config", str(config),
        "--instances.type", "file",
        "--instances.path", str(single_json),
        "--output_dir", str(out_dir),
        "--num_workers", str(num_workers),
        "--instances.deployment.type", "docker",
        "--instances.deployment.python_standalone_dir", "",
        '--instances.deployment.docker_args=["--add-host=host.docker.internal:host-gateway"]',
    ]
    print(f"    cmd: sweagent run-batch ... {iid}")
    # List-form invocation of the python interpreter against sweagent;
    # no shell interpretation. Paths originate from script-controlled
    # locations under PROJECT_ROOT.
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT))  # nosec B603  # nosem
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instances", default=str(RESULTS_DIR / "test99_instances.json"))
    ap.add_argument("--mem-port", type=int, default=8025)
    ap.add_argument("--no-cleanup", action="store_true", help="Keep images")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=int, default=0, help="Skip first N instances")
    args = ap.parse_args()

    instances = json.load(open(args.instances))
    if args.limit:
        instances = instances[args.start:args.start + args.limit]
    else:
        instances = instances[args.start:]
    print(f"Total instances to process: {len(instances)}")

    methods = {
        "no_context": gen_no_context_config(
            RESULTS_DIR / "no_context" / "config.yaml"),
        "contextgraph_summary": gen_contextgraph_config(
            RESULTS_DIR / "contextgraph_summary" / "config.yaml", args.mem_port),
    }
    out_dirs = {m: RESULTS_DIR / m / "output" for m in methods}

    dockerfile_path = str(PROJECT_ROOT / "scripts" / "swectx_patched.Dockerfile")
    log = []
    t_start = time.time()
    for i, inst in enumerate(instances, 1):
        iid = inst["instance_id"]
        base_image = inst["image_name"]
        # Build a tag-safe version of the base image name as the patched tag.
        # base_image like jiayuanz3/swecontextbench:django.django-13658
        # -> swectx-patched:django.django-13658
        patched_image = "swectx-patched:" + base_image.split(":", 1)[1]
        elapsed = time.time() - t_start
        rate = i / elapsed if elapsed > 0 else 0
        eta = (len(instances) - i + 1) / rate if rate > 0 else 0
        print(f"\n{'='*72}\n[{i}/{len(instances)}] {iid}  (elapsed {elapsed/60:.1f}m, eta {eta/60:.0f}m)\n{'='*72}")

        # Skip if both trajs exist
        all_done = all((out_dirs[m] / iid / f"{iid}.traj").exists()
                       for m in methods)
        if all_done:
            print(f"  [{iid}] both trajs exist, skip")
            log.append({"iid": iid, "status": "skip_existing"})
            continue

        # Ensure base image is pulled (build needs it)
        if not docker_image_exists(base_image):
            t0 = time.time()
            if not docker_pull(base_image):
                print(f"  [{iid}] base pull FAILED, skip")
                log.append({"iid": iid, "status": "pull_failed"})
                continue
            print(f"  [{iid}] base pulled in {time.time()-t0:.1f}s")

        # Build patched derivative
        if not docker_image_exists(patched_image):
            t0 = time.time()
            ok = docker_build_patched(base_image, patched_image,
                                      dockerfile_path, str(PROJECT_ROOT))
            if not ok:
                print(f"  [{iid}] build FAILED, skip")
                log.append({"iid": iid, "status": "build_failed"})
                continue
            print(f"  [{iid}] patched built in {time.time()-t0:.1f}s")

        # Rewrite instance to point at patched image
        inst_patched = dict(inst, image_name=patched_image)
        single_path = RESULTS_DIR / f"_single_{iid}.json"
        single_path.write_text(json.dumps([inst_patched]))

        for method, cfg in methods.items():
            traj = out_dirs[method] / iid / f"{iid}.traj"
            if traj.exists():
                print(f"  [{iid}] {method} traj exists, skip")
                continue
            t1 = time.time()
            rc = run_swe_agent(cfg, single_path, out_dirs[method], iid)
            print(f"  [{iid}] {method} done rc={rc} in {time.time()-t1:.1f}s")
            log.append({"iid": iid, "method": method, "rc": rc,
                        "secs": round(time.time()-t1, 1)})

        single_path.unlink(missing_ok=True)

        # No cleanup: 372GB on /home/jie/ext0/docker can fit ~100 images.

        # Snapshot log every 5 instances
        if i % 5 == 0:
            (RESULTS_DIR / "3way_log.json").write_text(json.dumps(log, indent=2))

    (RESULTS_DIR / "3way_log.json").write_text(json.dumps(log, indent=2))
    print(f"\nDone. Total elapsed {(time.time()-t_start)/60:.1f}m")
    for method in methods:
        n = sum(1 for x in (out_dirs[method]).glob("*/*.traj"))
        print(f"  {method}: {n} trajs")


if __name__ == "__main__":
    main()
