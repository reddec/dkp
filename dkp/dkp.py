#!/usr/bin/env python3
import stat
import json

from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from urllib.parse import quote
from shutil import copy as fs_copy, move as fs_move, copytree, copyfileobj
from datetime import datetime
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Dict, List, Set, Union


@dataclass
class Compose:
    # parsed compose file (aggregated)
    parsed: Dict
    # list of source compose files
    files: List[Path]

    @property
    def name(self) -> str:
        """
        Normalized project name
        """
        return self.parsed["name"]

    @cached_property
    def environments(self) -> Dict[str, Dict]:
        """
        Parsed environment variables per service.
        """
        ans = {}
        for name, service in self.parsed.get("services", {}).items():
            ans[name] = service.get("environment", {})
        return ans

    @cached_property
    def has_conflicted_files(self) -> bool:
        """
        True if there are at least two docker-compose files with the same base names
        """
        names = set()
        for file in self.files:
            if file.name in names:
                return True
            names.add(file.name)
        return False

    @cached_property
    def work_dir(self) -> Path:
        """
        Main working directory based on first docker-compose file.
        """
        return self.files[0].absolute().parent

    @cached_property
    def volumes(self) -> Set[str]:
        """
        List of all known volumes within project
        """
        ans = set()
        for volume in self.parsed.get("volumes", {}).values():
            ans.add(volume["name"])
        return ans

    @cached_property
    def binds(self) -> Set[Path]:
        """
        List of all known binds (non-volume mounts)
        """
        ans = set()
        for service in self.parsed.get("services", {}).values():
            for volume in service.get("volumes", []):
                if volume["type"] == "bind":
                    ans.add(Path(volume["source"]).absolute())
        return ans

    @cached_property
    def images(self) -> Set[str]:
        """
        List of all used images in services
        """
        ans = set()
        for service in self.parsed.get("services", {}).values():
            image = service.get("image")
            if image is not None:
                ans.add(image)
        return ans


def is_relative_to(src: Path, dest: Path) -> bool:
    """
    Checks if dest is relative to src. Same as Path.is_relative_to
    but with shim for python 3.8
    """
    src = src.absolute()
    dest = dest.absolute()
    if hasattr(dest, "is_relative_to"):
        return dest.is_relative_to(src)
    # shim for 3.8 for Path.is_relative_to
    try:
        dest.relative_to(src)
        return True
    except:
        return False


def inspect(project_name: str) -> Compose:
    """
    Parse docker-compose project by name.
    It doesn't matter which working directory is used for the module,
    the function will inspect project metadata to find source manifests.
    """
    # get config files
    res = run(
        [
            "docker",
            "compose",
            "ls",
            "--format",
            "json",
            "-a",
            "--filter",
            f"Name=^{project_name}$",
        ],
        check=True,
        capture_output=True,
    )
    items = json.loads(res.stdout)
    assert len(items) > 0, f"project {project_name} not found"

    # normalize and parse
    args = []
    files: List[Path] = []
    for x in items[0]["ConfigFiles"].split(","):
        file = Path(x.strip()).absolute()
        files.append(file)
        args += ["-f", file]

    parsed = json.loads(
        run(
            ["docker", "compose", "-p", project_name]
            + args
            + ["config", "--no-interpolate", "--format", "json"],
            check=True,
            capture_output=True,
        ).stdout
    )
    return Compose(parsed=parsed, files=files)


def template_local(file: Union[str, Path], **args) -> str:
    """
    Read local (to current module) file and template it using %%<key>%% format.
    """
    file = Path(__file__).parent / file
    content = file.read_text()
    for k, v in args.items():
        content = content.replace(f"%%{k}%%", v)
    return content


def header_script(**args) -> str:
    """
    Generate archive header script.
    """
    return template_local("header.sh", **args)


def backup_volume(volume: Union[str, Path], output: Path, image="busybox"):
    """
    Backup single volume using tar and temporary docker container.
    """
    output = output.absolute()
    archive_name = output.name
    mount_path = output.parent
    args = []
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/input:ro",
            "-v",
            f"{mount_path}:/output",
            image,
            "tar",
            "-C",
            "/input",
            "-cf",
            f"/output/{archive_name}",
        ]
        + args
        + [
            ".",
        ],
        check=True,
    )


def archive_dir(path: Path, output: Path):
    """
    Archive and compress directory using tar with gzip
    """
    run(
        ["tar", "-C", path.absolute(), "-zcf", output.absolute(), "."],
        check=True,
    )


def encrypt(path: Path, output: Path, passphrase: str):
    """
    Encrypt file using gpg with provided passphrase (symmetric key) and writes encrypted content
    to new file (output).
    """
    run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--passphrase",
            passphrase,
            "--output",
            output.absolute(),
            "--symmetric",
            path.absolute(),
        ],
        check=True,
    )


def make_executable(path: Path):
    """
    Make file executable. Same as chmod +x
    """
    st = path.stat()
    path.chmod(st.st_mode | stat.S_IEXEC)


def gen_scripts(
    work_dir: Path,
    info: Compose,
    sources: List[Path],
):
    """
    Generate restoration scripts.
    """
    src_args = " ".join(f"-f {p.name!r}" for p in sources)
    if len(sources) == 1 and sources[0].name in (
        "docker-compose.yaml",
        "docker-compose.yml",
    ):
        # no need for extra args to start services
        nice_args = ""
    else:
        nice_args = " " + src_args
    restore = work_dir / "restore.sh"
    restore.write_text(
        template_local(
            "restore.sh",
            PROJECT_NAME=info.name,
            SOURCE_ARGS=nice_args,
        )
    )
    make_executable(restore)


def backup(
    project_name: str,
    output: Path,
    password: Union[str, None] = None,
    skip_images=False,
):
    """
    Backup docker compose project. It includes volumes, images, mounted directories and files and envs.
    Genrates encrypted self-executable archive.
    """
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)

    info = inspect(project_name)
    sources: List[Path] = []
    images: List[Path] = []
    with TemporaryDirectory(
        dir=output.parent, prefix=f".{project_name}.", suffix=".pack.tmp"
    ) as work_dir:
        root_dir = Path(work_dir)
        # add top-level dir for simpler unpacking
        work_dir = root_dir / project_name

        if not skip_images:
            # copy images
            for image in info.images:
                image_file = work_dir / "images" / (quote(image, safe="") + ".tar")
                image_file.parent.mkdir(exist_ok=True, parents=True)
                images.append(image_file)
                print("saving image", image, "to", image_file.relative_to(work_dir))
                run(["docker", "image", "save", "-o", image_file, image], check=True)

        # copy volumes
        for volume in info.volumes:
            volume_file = work_dir / "volumes" / (volume + ".tar")
            volume_file.parent.mkdir(exist_ok=True, parents=True)
            print("saving volume", volume, "to", volume_file.relative_to(work_dir))
            backup_volume(volume, volume_file)

        project_dir = work_dir / "project" / info.name
        # copy manifests
        for i, source in enumerate(info.files):
            source_name = source.name
            if info.has_conflicted_files:
                # to avoid collisions
                source_name = f"{i}_{source_name}"

            source_file = project_dir / source_name
            source_file.parent.mkdir(exist_ok=True, parents=True)
            sources.append(source_file)
            print("saving config", source_name, "to", source_file.relative_to(work_dir))
            fs_copy(source, source_file)

        # copy relative binds
        for bind in info.binds:
            if not is_relative_to(info.work_dir, bind):
                print("skipping", str(bind), "-", "absolute mount path")
                continue
            rel_path = bind.relative_to(info.work_dir)
            dest_path = project_dir / rel_path
            print("copying", bind, "to", dest_path.relative_to(work_dir))
            if bind.resolve().is_dir():
                copytree(bind, dest_path, dirs_exist_ok=True)
            else:
                dest_path.parent.mkdir(exist_ok=True, parents=True)
                fs_copy(bind, dest_path, follow_symlinks=True)

        # copy env files
        file: Path
        for file in info.work_dir.iterdir():
            if (
                file.name != ".env" and file.suffix != ".env"
            ) or file.resolve().is_dir():
                continue
            env_file = project_dir / file.name
            env_file.parent.mkdir(exist_ok=True, parents=True)
            print(
                "found env file",
                file.name,
                "- copying to",
                env_file.relative_to(work_dir),
            )
            fs_copy(file.name, env_file)

        # add restore script
        gen_scripts(work_dir, info, sources)

        print("archiving...")
        archive_file = output.with_name(output.name + ".tar.gz")
        archive_dir(root_dir.absolute(), archive_file)

        print("encrypting...")
        encrypted_file = output.with_name(archive_file.name + ".gpg")
        encrypt(archive_file, encrypted_file, password)
        # replace archive
        fs_move(encrypted_file, archive_file)
        print("finalizing...")
        # create self extract script
        with output.open("wb") as out, archive_file.open("rb") as archive:

            out.write(
                header_script(
                    TODAY=datetime.utcnow().isoformat(),
                    PROJECT_NAME=project_name,
                ).encode()
            )
            copyfileobj(archive, out)

        archive_file.unlink()
        make_executable(output)


def main():
    """
    CLI wrapper for backup
    """
    from argparse import ArgumentParser
    from os import getenv

    default_project = Path.cwd().name
    default_file = Path(Path.cwd().name + ".bin")

    parser = ArgumentParser(
        "dkp",
        description="Docker Compose packer - pack compose project with all batteries included",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=default_file,
        help=f"Output file. Default {default_file}",
        type=Path,
    )
    parser.add_argument(
        "--skip-images",
        "-S",
        action="store_true",
        help="Do not archive images",
        default=False,
    )
    parser.add_argument(
        "--passphrase",
        "-p",
        default=getenv("PASSPHRASE"),
        help="Passphrase to encrypt backup. Can be set via env PASSPHRASE",
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=default_project,
        help=f"Compose project name. Default is {default_project}",
    )
    args = parser.parse_args()
    assert (
        args.passphrase is not None and args.passphrase != ""
    ), "Passphrase is not set via argument neither via PASSPHRASE environment variable"

    backup(
        args.project,
        args.output,
        password=args.passphrase,
        skip_images=args.skip_images,
    )


if __name__ == "__main__":
    main()
