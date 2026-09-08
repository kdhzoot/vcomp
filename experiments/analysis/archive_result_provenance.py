#!/usr/bin/env python3
"""Archive small, immutable provenance for a completed Chapter 2/3 campaign.

Commands are selected from the promoted loads/reads, including reused controls.
No DB, benchmark log, identity listing, or binary is copied. Source paths below
the artifacts root are preserved under RESULT_DIR/provenance/. Existing copies
must match exactly; this command never refreshes an archive in place.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


CONTEXT_NAMES = (
    "clean.commit", "clean.status", "clean.diff",
    "f2load.commit", "f2load.status", "f2load.diff", "environment.txt",
)
MAX_FILE_BYTES = 5 * 1024 * 1024


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(path, root):
    resolved = Path(path).resolve(strict=True)
    resolved.relative_to(root)
    return resolved


def collect(run_root, result_dir, artifact_root):
    source_manifest = read_json(run_root / "manifest.json")
    result_manifest = read_json(result_dir / "manifest.json")
    if source_manifest != result_manifest:
        raise ValueError("source and promoted campaign manifests differ")
    if source_manifest.get("phase") != "full":
        raise ValueError("only completed full campaigns may be archived")
    audit = read_json(result_dir / "FINAL_AUDIT.json")
    loads = read_json(result_dir / "loads.json")
    reads = read_json(result_dir / "reads.json")
    if (audit.get("status", "passed") != "passed" or
            audit.get("validated_loads") != len(loads) or
            audit.get("validated_reads") != len(reads)):
        raise ValueError("the final audit does not validate the promoted matrix")
    if not any((run_root / name).is_file()
               for name in ("COMPLETED", "NON_F2_COMPLETED.json")):
        raise ValueError("source campaign has no completion marker")

    files = set()
    campaigns = {run_root}

    def add_commands(directory):
        directory = inside(directory, artifact_root)
        relative = directory.relative_to(artifact_root)
        if (len(relative.parts) < 4 or relative.parts[0] not in
                ("log_loads", "log_runs") or relative.parts[2] != "full"):
            raise ValueError("unexpected campaign command path: " + str(directory))
        campaigns.add(inside(artifact_root / "log_loads" /
                             relative.parts[1] / "full", artifact_root))
        for name in ("command.json", "command.sh"):
            files.add(inside(directory / "raw" / name, artifact_root))

    for row in loads.values():
        if row.get("status") != "validated":
            raise ValueError("unvalidated load in promoted results")
        for phase in row["phases"]:
            add_commands(phase["log_dir"])
    for row in reads.values():
        if row.get("status") != "ok":
            raise ValueError("unvalidated read in promoted results")
        add_commands(row["result_dir"])

    for campaign in campaigns:
        for name in CONTEXT_NAMES:
            files.add(inside(campaign / name, artifact_root))
        snapshots = sorted(campaign.glob("*.py"))
        if not snapshots:
            raise ValueError("frozen runner/library snapshots are missing")
        files.update(inside(path, artifact_root) for path in snapshots)
        available_hashes = {sha256(path) for path in snapshots}
        manifest = read_json(campaign / "manifest.json")
        for key in ("runner_sha256", "library_sha256", "parent_runner_sha256"):
            if key in manifest and manifest[key] not in available_hashes:
                raise ValueError("frozen source hash mismatch: " + key)

    return source_manifest["run_id"], sorted(files)


def archive(run_root, result_dir):
    run_root = run_root.resolve(strict=True)
    result_dir = result_dir.resolve(strict=True)
    if run_root.name != "full" or run_root.parent.parent.name != "log_loads":
        raise ValueError("--run-root must be artifacts/log_loads/RUN_ID/full")
    artifact_root = run_root.parents[2]
    run_id, files = collect(run_root, result_dir, artifact_root)
    destination_root = result_dir / "provenance"
    if destination_root.is_symlink():
        raise ValueError("provenance destination cannot be a symlink")

    records, planned = [], []
    for source in files:
        if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("not a small evidence file: " + str(source))
        data = source.read_bytes()
        data.decode("utf-8")
        if b"\0" in data:
            raise ValueError("binary evidence is not accepted: " + str(source))
        relative = source.relative_to(artifact_root)
        destination = destination_root / relative
        # Existing ancestor symlinks must not redirect writes outside the bundle.
        destination.resolve().relative_to(destination_root.resolve())
        digest = hashlib.sha256(data).hexdigest()
        if destination.exists() and (not destination.is_file() or
                                     sha256(destination) != digest):
            raise ValueError("conflicting archived file: " + str(destination))
        records.append({"source_path": relative.as_posix(),
                        "archive_path": "provenance/" + relative.as_posix(),
                        "bytes": len(data), "sha256": digest})
        planned.append((source, destination, digest))

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "source_artifact_root": str(artifact_root),
        "selection": "Validated load/read commands and their frozen campaign context; includes reused controls.",
        "file_count": len(records),
        "total_bytes": sum(row["bytes"] for row in records),
        "files": records,
    }
    manifest_path = destination_root / "manifest.json"
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    if manifest_path.exists() and manifest_path.read_bytes() != manifest_bytes:
        raise ValueError("conflicting provenance manifest: " + str(manifest_path))

    # All source hashes and destination conflicts are checked before any copy.
    copied = 0
    for source, destination, digest in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with source.open("rb") as source_stream, destination.open("xb") as target:
                shutil.copyfileobj(source_stream, target)
            copied += 1
        if sha256(destination) != digest:
            raise ValueError("archive hash verification failed: " + str(destination))
    if not manifest_path.exists():
        with manifest_path.open("xb") as target:
            target.write(manifest_bytes)
    print(json.dumps({"run_id": run_id, "verified_files": len(records),
                      "copied_files": copied, "bytes": manifest["total_bytes"],
                      "manifest": str(manifest_path)}, sort_keys=True))


def verify(result_dir):
    """Verify a cloned archive without opening any original artifact path."""
    result_dir = result_dir.resolve(strict=True)
    manifest = read_json(result_dir / "provenance" / "manifest.json")
    records = manifest["files"]
    if len(records) != manifest["file_count"]:
        raise ValueError("provenance file count mismatch")
    if len({row["archive_path"] for row in records}) != len(records):
        raise ValueError("duplicate provenance paths")
    total = 0
    for row in records:
        path = inside(result_dir / row["archive_path"], result_dir / "provenance")
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError("archive hash/size mismatch: " + str(path))
        total += row["bytes"]
    if total != manifest["total_bytes"]:
        raise ValueError("provenance byte count mismatch")
    print(json.dumps({"run_id": manifest["run_id"], "verified_files": len(records),
                      "bytes": total, "source_artifacts_required": False}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--result-dir", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true",
                        help="Check archive hashes without requiring source artifacts.")
    args = parser.parse_args()
    if args.verify_only:
        verify(args.result_dir)
    elif args.run_root is None:
        parser.error("--run-root is required when archiving")
    else:
        archive(args.run_root, args.result_dir)


if __name__ == "__main__":
    main()
