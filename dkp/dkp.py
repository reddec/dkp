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
from typing import Dict, List, Optional, Set, Union


@dataclass
class Compose:
    # parsed compose file (aggregated)
    parsed: Dict

    # parsed output of "docker compose images"
    # Entry example:
    #  {
    #    "ID": "sha256:ba5dc23f6...",
    #    "ContainerName": "test-data-dummy-1",
    #    "Repository": "busybox",
    #    "Tag": "latest",
    #    "Size": 4261550
    #  },
    parsed_images: List[Dict]

    # list of source compose files
    files: List[Path]

    # list of environment files
    env_files: List[Path]

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
        for image_entry in self.parsed_images:
            repository = image_entry.get("Repository")
            tag = image_entry.get("Tag")
            image = f'{repository}:{tag}'
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


def inspect(
    project_name: str,
    all_images: bool,
    env_files: List[Path]
) -> Compose:
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
    args: List[Union[str, Path]] = []
    files: List[Path] = []
    for x in items[0]["ConfigFiles"].split(","):
        file = Path(x.strip()).absolute()
        files.append(file)
        args += ["-f", file]
    for env_file in env_files:
        args += ['--env-file', env_file]

    parsed = json.loads(
        run(
            ["docker", "compose", "-p", project_name]
            + args
            + ["config", "--no-interpolate", "--format", "json"],
            check=True,
            capture_output=True,
        ).stdout
    )

    if all_images:
        parsed_images = json.loads(
            run(
                ["docker", "compose", "-p", project_name]
                + args
                + ["images", "--format", "json"],
                check=True,
                capture_output=True,
            ).stdout
        )
    else:
        parsed_images = []

    return Compose(
        parsed=parsed,
        parsed_images=parsed_images,
        files=files,
        env_files=env_files
    )


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
    args: List[str] = []
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

    for env_file in info.env_files:
        nice_args += f" --env-file '{env_file}'"

    restore = work_dir / "restore.sh"
    restore.write_text(
        template_local(
            "restore.sh",
            PROJECT_NAME=info.name,
            SOURCE_ARGS=nice_args
        )
    )
    make_executable(restore)


def backup(
    project_name: str,
    output: Path,
    password: Optional[str],
    skip_images=False,
    all_images: bool = False,
    env_files: List[Path] = [],
):
    """
    Backup docker compose project. It includes volumes, images, mounted directories and files and envs.
    Genrates encrypted self-executable archive.
    """
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)

    info = inspect(project_name, all_images, env_files)
    sources: List[Path] = []
    images: List[Path] = []
    with TemporaryDirectory(
        dir=output.parent, prefix=f".{project_name}.", suffix=".pack.tmp"
    ) as work_dir_str:
        root_dir = Path(work_dir_str)
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

        # Collect env files.
        #
        # All file names in "all_env_files" are absolute and resolved for
        # symbolic links.
        all_env_files: set[Path] = {file.resolve() for file in env_files}

        # Use the default env file (called .env) if it exists and no custom env
        # file was specified with "--env-file".
        dot_env: Path = (info.work_dir / '.env').resolve()
        if env_files == [] and dot_env.is_file():
            all_env_files.add(dot_env)

        # Add "*.env" files. Probably they are used in the "env_file" attribute
        # of the composed file.
        for file in info.work_dir.iterdir():
            if (file.suffix == ".env") and file.resolve().is_file():
                all_env_files.add(file.resolve())

        # Copy the collected env files
        env_file: Path
        for env_file in sorted(all_env_files):
            if not is_relative_to(info.work_dir, env_file.resolve()):
                print(f"skipping env file {env_file} - outside of the project directory")
                continue
            rel_target_path: Path = env_file.absolute().relative_to(info.work_dir.absolute())
            target: Path = project_dir / rel_target_path
            target.parent.mkdir(exist_ok=True, parents=True)
            print(f"copying env file {env_file.name} to "
                  f"{target.relative_to(work_dir)}")
            fs_copy(env_file, target)

        # add restore script
        gen_scripts(work_dir, info, sources)

        print("archiving...")
        archive_file = output.with_name(output.name + ".tar.gz")
        archive_dir(root_dir.absolute(), archive_file)

        if password:
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
        "--all-images",
        action="store_true",
        default=False,
        help=("""Export all images. Without this option, only the images of
              those services are exported that have an 'image' key in the
              compose file."""),
    )
    parser.add_argument(
        "--passphrase",
        "-p",
        default=getenv("PASSPHRASE"),
        help="Passphrase to encrypt backup. Can be set via env PASSPHRASE",
    )
    parser.add_argument(
        "--env-file",
        nargs="*",
        default=[],
        help="Environment file(s) that should be passed to Compose with '--env-file'",
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=default_project,
        help=f"Compose project name. Default is {default_project}",
    )
    args = parser.parse_args()

    if args.passphrase == "":
        args.passphrase = None

    env_files = [Path(env_file) for env_file in args.env_file]
    backup(
        args.project,
        args.output,
        password=args.passphrase,
        skip_images=args.skip_images,
        all_images=args.all_images,
        env_files=env_files
    )


if __name__ == "__main__":
    main()
